/**
 * AI-Echo 统一 API 客户端  v6
 * ========================
 * 升级日志 v6:
 *   [新增] authClient — 注册、登录、登出、获取当前用户
 *   [新增] apiFetch 自动从 sessionStorage 读取 JWT Token，注入 Authorization 头
 *   [新增] 401 响应自动清除 Token 并触发页面事件（供 App.jsx 监听跳转登录）
 *
 * 用法:
 *   import { apiClient, authClient } from './api'
 *   const result = await apiClient.valuate({ asset_category, description, ... })
 *   await authClient.login({ username, password })
 */

// ── 后端地址（开发时由 Vite 代理，生产时从环境变量读取）────────────
const BASE_URL = import.meta.env.PROD
  ? (import.meta.env.VITE_API_URL || '')
  : '';

// ── 默认超时（ms）──────────────────────────────────────────────────
const DEFAULT_TIMEOUT_MS = 8000;

// ── Token 管理（存入 sessionStorage，关闭标签页自动清除）──────────
const TOKEN_KEY    = 'zszq_token';
const CREATOR_KEY  = 'zszq_creator';

export const tokenStore = {
  get:   ()        => sessionStorage.getItem(TOKEN_KEY),
  set:   (t, info) => {
    sessionStorage.setItem(TOKEN_KEY, t);
    if (info) sessionStorage.setItem(CREATOR_KEY, JSON.stringify(info));
  },
  clear: ()        => {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(CREATOR_KEY);
  },
  getCreator: () => {
    try { return JSON.parse(sessionStorage.getItem(CREATOR_KEY) || 'null'); }
    catch { return null; }
  },
};

// ── 结构化错误类型 ─────────────────────────────────────────────────
export class ApiError extends Error {
  constructor(message, type = 'UNKNOWN', status = null) {
    super(message);
    this.name  = 'ApiError';
    this.type  = type;   // 'TIMEOUT' | 'NETWORK' | 'SERVER' | 'REJECTED' | 'AUTH'
    this.status = status;
  }
}

// ── 核心 fetch 包装（统一超时 + 错误分类 + 自动注入 Token）─────────
async function apiFetch(path, options = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  // 自动注入 JWT
  const authHeaders = {};
  const token = tokenStore.get();
  if (token) authHeaders['Authorization'] = `Bearer ${token}`;

  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders,
        ...options.headers,
      },
    });
    clearTimeout(timer);

    if (res.status === 401) {
      tokenStore.clear();
      window.dispatchEvent(new CustomEvent('auth:logout'));
      throw new ApiError('登录已过期，请重新登录', 'AUTH', 401);
    }
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new ApiError(
        `HTTP ${res.status}: ${body.slice(0, 200)}`,
        'SERVER',
        res.status
      );
    }
    return await res.json();
  } catch (err) {
    clearTimeout(timer);
    if (err instanceof ApiError) throw err;
    if (err.name === 'AbortError') {
      throw new ApiError(`后端连接超时（${timeoutMs / 1000}s）`, 'TIMEOUT');
    }
    throw new ApiError(err.message || '网络错误', 'NETWORK');
  }
}

// ── 认证客户端（注册 / 登录 / 登出）──────────────────────────────
export const authClient = {
  async register({ username, password, display_name = '', email = '' }) {
    const data = await apiFetch('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify({ username, password, display_name, email }),
    });
    tokenStore.set(data.access_token, {
      creator_id: data.creator_id,
      username: data.username,
      display_name: data.display_name,
    });
    return data;
  },

  async login({ username, password }) {
    const data = await apiFetch('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
    tokenStore.set(data.access_token, {
      creator_id: data.creator_id,
      username: data.username,
      display_name: data.display_name,
    });
    return data;
  },

  logout() {
    tokenStore.clear();
    window.dispatchEvent(new CustomEvent('auth:logout'));
  },

  async me() {
    return apiFetch('/api/auth/me', { method: 'GET' });
  },

  isLoggedIn() {
    return !!tokenStore.get();
  },

  currentCreator() {
    return tokenStore.getCreator();
  },
};

// ── 指数退避重试（TIMEOUT / NETWORK 类型自动重试，SERVER 类不重试）──
async function withRetry(fn, retries = 2) {
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      const isRetryable = err instanceof ApiError && err.type !== 'SERVER';
      if (attempt === retries || !isRetryable) throw err;
      const delay = 300 * Math.pow(2, attempt);   // 300ms, 600ms
      await new Promise(r => setTimeout(r, delay));
    }
  }
}

// ── 请求去重 Map（同 key 的并发请求合并为一次）──────────────────────
const _inflight = new Map();

async function dedupe(key, fn) {
  if (_inflight.has(key)) return _inflight.get(key);
  const promise = fn().finally(() => _inflight.delete(key));
  _inflight.set(key, promise);
  return promise;
}

// ── API 方法集合 ───────────────────────────────────────────────────
export const apiClient = {

  /**
   * 健康检查 — 用于前端展示后端连接状态
   * 返回: { status, version, corpus_size, db_stats, ... }
   */
  async health() {
    return dedupe('health', () =>
      withRetry(() => apiFetch('/api/health', { method: 'GET' }, 3000), 1)
    );
  },

  /**
   * 多模态资产估值（核心端点）
   * @param {object} payload
   *   - asset_category: 'text' | 'image' | 'audio' | 'video'
   *   - description: string
   *   - is_zk_mode: boolean
   *   - scene_override: string | null
   *   - audio_data: base64 string | null
   *   - image_data: base64 string | null
   *   - video_data: base64 string | null  (★ v5: MP4/MOV/WEBM, VideoAdapter Stage B)
   * 返回: valuationResult 对象（与 OracleValuationScreen 期待格式完全对齐）
   */
  /**
   * 估值超时设置：
   *   text/image — 8s（DEFAULT_TIMEOUT_MS）
   *   audio/video — 30s（模型加载 + 帧采样较慢）
   */
  async valuate(payload) {
    const heavy = ['audio', 'video'].includes(payload.asset_category);
    const timeout = heavy ? 30_000 : DEFAULT_TIMEOUT_MS;
    return withRetry(
      () => apiFetch('/api/valuate', {
        method: 'POST',
        body:   JSON.stringify(payload),
      }, timeout),
      1   // 重试 1 次（估值最多尝试 2 次，避免重复计费）
    );
  },

  /**
   * 估值历史记录
   * @param {number} limit  最多返回条数（默认20）
   * @param {string} modality  按模态过滤（可选）
   */
  async history(limit = 20, modality = '') {
    const params = new URLSearchParams({ limit });
    if (modality) params.set('modality', modality);
    return apiFetch(`/api/history?${params}`, { method: 'GET' });
  },

  /**
   * 单条历史详情
   * @param {number} id
   */
  async historyDetail(id) {
    return apiFetch(`/api/history/${id}`, { method: 'GET' });
  },

  /**
   * 历史记录全文搜索（v2 新增，对应 storage.search_history）
   * @param {string} q  搜索关键词
   * @param {number} limit
   */
  async historySearch(q, limit = 20) {
    const params = new URLSearchParams({ q, limit });
    return apiFetch(`/api/history/search?${params}`, { method: 'GET' });
  },

  /**
   * 删除单条历史记录（v2 新增，对应 DELETE /api/history/{id}）
   * @param {number} id
   */
  async deleteHistory(id) {
    return apiFetch(`/api/history/${id}`, { method: 'DELETE' });
  },

  /**
   * 详细统计（v2 新增，对应 /api/stats）
   * 返回: { stats: { total, avg_quality, by_modality, top_scenes }, top_assets, corpus_size }
   */
  async stats() {
    return apiFetch('/api/stats', { method: 'GET' }, 5000);
  },

  /**
   * Top-N 高价值资产排行榜（v2 新增，对应 /api/top）
   * @param {number} limit
   * @param {string} modality  按模态过滤（可选）
   */
  async topAssets(limit = 10, modality = '') {
    const params = new URLSearchParams({ limit });
    if (modality) params.set('modality', modality);
    return apiFetch(`/api/top?${params}`, { method: 'GET' });
  },

  /**
   * 场景配置（前端场景选项动态化用）
   */
  async scenes() {
    return apiFetch('/api/scenes', { method: 'GET' }, 3000);
  },

  /**
   * 批量估值（Stage 2 新增）
   * @param {Array<object>} items  ValuationRequest 数组，最多 20 条
   * 返回: { results, total, ok, errors }
   */
  async batchValuate(items) {
    return withRetry(
      () => apiFetch('/api/batch_valuate', {
        method: 'POST',
        body:   JSON.stringify({ items }),
      }, 60_000),  // 批量最多 60s
      0   // 批量不重试（避免重复计费）
    );
  },
};

// ── useApiHealth Hook —— 供顶栏展示后端状态 ──────────────────────
// 使用方式: const { status, corpusSize } = useApiHealth()
import { useState, useEffect, useCallback, useRef } from 'react';

export function useApiHealth() {
  const [status, setStatus]         = useState('checking'); // 'checking'|'online'|'offline'
  const [version, setVersion]       = useState(null);
  const [corpusSize, setCorpusSize] = useState(null);
  const [error, setError]           = useState(null);

  const check = useCallback(async () => {
    setStatus('checking');
    try {
      const data = await apiClient.health();
      setStatus('online');
      setVersion(data.version);
      setCorpusSize(data.corpus_size ?? null);
      setError(null);
    } catch (e) {
      setStatus('offline');
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    check();
    // 每 30 秒轮询一次（不要太频繁）
    const interval = setInterval(check, 30_000);
    return () => clearInterval(interval);
  }, [check]);

  return { status, version, corpusSize, error, recheck: check };
}

// ── useWsValuate Hook — WebSocket 实时估值进度 ─────────────────────────
// 用法:
//   const { connect, progress, result, error, isConnecting } = useWsValuate()
//   connect(payload)   // 触发估值，payload = { asset_category, description, ... }
//   progress           // [{ stage, pct, msg }, ...]
//   result             // 最终估值结果（同 /api/valuate 格式）
//   error              // 错误信息

// ── useWsValuate Hook — WebSocket 实时估值进度 ─────────────────────────

export function useWsValuate() {
  const [progress,     setProgress]     = useState([]);
  const [result,       setResult]       = useState(null);
  const [error,        setError]        = useState(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const wsRef = useRef(null);  // 存放 WebSocket 实例，供 disconnect() 使用

  /** 主动断开当前 WebSocket 连接 */
  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setIsConnecting(false);
  }, []);

  /**
   * 发起 WebSocket 估值
   * @param {object} payload  与 /api/valuate 相同的请求体
   */
  const connect = useCallback((payload) => {
    // 断开上一次未结束的连接
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    setProgress([]);
    setResult(null);
    setError(null);
    setIsConnecting(true);

    // 开发时走 Vite proxy（vite.config.js 中配置 /ws → localhost:8000）
    // 生产时走同源 wss://
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = import.meta.env.PROD
      ? `${protocol}//${window.location.host}/ws/valuate`
      : `${protocol}//localhost:8000/ws/valuate`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify(payload));
    };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'progress') {
          setProgress(prev => [...prev, { stage: msg.stage, pct: msg.pct, msg: msg.msg }]);
        } else if (msg.type === 'result') {
          setResult(msg.data);
          setIsConnecting(false);
          wsRef.current = null;
        } else if (msg.type === 'error') {
          setError(msg.detail || '未知错误');
          setIsConnecting(false);
          wsRef.current = null;
        }
      } catch (_) { /* 忽略格式错误的帧 */ }
    };

    ws.onerror = () => {
      setError('WebSocket 连接失败，后端可能未启动或不支持 WebSocket');
      setIsConnecting(false);
      wsRef.current = null;
    };

    ws.onclose = () => {
      setIsConnecting(false);
      wsRef.current = null;
    };
  }, []);

  // 组件卸载时自动清理连接
  useEffect(() => {
    return () => {
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  /** 当前进度百分比（取最新一帧的 pct，默认 0） */
  const currentPct = progress.length > 0 ? progress[progress.length - 1].pct : 0;

  /** 当前阶段描述（取最新一帧的 msg） */
  const currentMsg = progress.length > 0 ? progress[progress.length - 1].msg : '';

  return { connect, disconnect, progress, currentPct, currentMsg, result, error, isConnecting };
}

// ── detectCollision — 相似资产碰撞检测 ──────────────────────────────
// 调用 POST /api/detect_collision
// params: { description, asset_category, embedding?, exclude_hash?, top_k? }
export async function detectCollision(params) {
  return apiFetch('/api/detect_collision', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}


// ════════════════════════════════════════════════════════════════════
// v3 新增：数据集目录 + 创作者收益 API 客户端
// ════════════════════════════════════════════════════════════════════

export const datasetClient = {

  // ── 数据集目录（买家侧）─────────────────────────────────────────

  /** 列出所有数据集包（公开目录） */
  async listPackages(limit = 50) {
    return apiFetch(`/api/dataset/packages?limit=${limit}`, { method: 'GET' });
  },

  /** 数据集包详情 */
  async getPackage(packageId) {
    return apiFetch(`/api/dataset/package/${packageId}`, { method: 'GET' });
  },

  /** 记录购买（触发分润） */
  async purchase(packageId, priceCny) {
    return apiFetch('/api/dataset/sell', {
      method: 'POST',
      body: JSON.stringify({
        package_id: packageId,
        buyer_id:   'buyer_' + Date.now(),
        price_cny:  priceCny,
      }),
    });
  },

  // ── 创作者侧 ────────────────────────────────────────────────────

  /** 上传单条素材（需登录）*/
  async ingest(materialType, rawContent, metadata = {}) {
    return apiFetch('/api/dataset/ingest', {
      method: 'POST',
      body: JSON.stringify({ material_type: materialType, raw_content: rawContent, metadata }),
    });
  },

  /** 列出当前创作者的素材（需登录）*/
  async listMaterials(limit = 50) {
    return apiFetch(`/api/dataset/materials?limit=${limit}`, { method: 'GET' });
  },

  /** 启动生产任务（需登录）*/
  async produce(materialIds, opts = {}) {
    return apiFetch('/api/dataset/produce', {
      method: 'POST',
      body: JSON.stringify({
        material_ids: materialIds,
        target_types: opts.targetTypes  ?? ['sft', 'dpo'],
        name:         opts.name         ?? '未命名数据集',
        description:  opts.description  ?? '',
        min_quality:  opts.minQuality   ?? 5.0,
        price_cny:    opts.priceCny     ?? 0,
      }),
    });
  },

  // ── 创作者收益 ──────────────────────────────────────────────────

  /** 当前登录创作者的收益汇总 */
  async myEarnings() {
    const creator = tokenStore.getCreator();
    if (!creator) throw new ApiError('未登录', 'AUTH');
    return apiFetch(`/api/creator/${creator.creator_id}/earnings`, { method: 'GET' });
  },

  /** 全平台收益排行榜 */
  async leaderboard(limit = 20) {
    return apiFetch(`/api/creator/leaderboard?limit=${limit}`, { method: 'GET' });
  },

  /** 平台整体统计 */
  async platformStats() {
    return apiFetch('/api/platform/stats', { method: 'GET' });
  },

  /** 任务列表 */
  async listJobs(limit = 50) {
    return apiFetch(`/api/dataset/jobs?limit=${limit}`, { method: 'GET' });
  },

  // ── v3 新增：SQLite 账本 & 监控（对应后端 P1/P0 升级）────────────

  /** 创作者余额（从 SQLite 账本读取，重启不丢） */
  async myBalance() {
    return apiFetch('/api/creator/balance', { method: 'GET' });
  },

  /** 创作者账本流水（SQLite，最近 N 条） */
  async myLedger(limit = 50) {
    return apiFetch(`/api/creator/ledger?limit=${limit}`, { method: 'GET' });
  },

  /** 平台流水线监控快照（阶段耗时 + 未解决告警） */
  async monitorSnapshot() {
    return apiFetch('/api/platform/monitor', { method: 'GET' });
  },

  /** 告警列表 */
  async alerts(includeResolved = false, limit = 50) {
    return apiFetch(
      `/api/platform/alerts?include_resolved=${includeResolved}&limit=${limit}`,
      { method: 'GET' }
    );
  },

  /** 标记告警已解决 */
  async resolveAlert(alertId) {
    return apiFetch(`/api/platform/alerts/${alertId}/resolve`, { method: 'POST' });
  },

  /** 数据集版本列表（SQLite 持久化）*/
  async listVersions(name = null, limit = 50) {
    const params = new URLSearchParams({ limit });
    if (name) params.set('name', name);
    return apiFetch(`/api/dataset/versions?${params}`, { method: 'GET' });
  },

  /** 版本 Diff */
  async versionDiff(fromId, toId) {
    return apiFetch(`/api/dataset/version/diff?from=${fromId}&to=${toId}`, { method: 'GET' });
  },

  /** 数据集包列表（SQLite 持久化，重启不丢） */
  async listPackagesSqlite(limit = 20) {
    return apiFetch(`/api/dataset/packages?limit=${limit}`, { method: 'GET' });
  },
};
