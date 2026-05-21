/**
 * AI-Echo 统一 API 客户端
 * ========================
 * 架构升级 v5 — 前后端真实联通层
 *
 * 解决的核心问题:
 *   - 原来每个组件各自写 fetch，超时/重试/错误处理逻辑散落
 *   - 生产环境需要动态切换后端地址
 *   - 无统一错误类型，前端无法区分"后端挂了"vs"业务拒绝"
 *
 * 用法:
 *   import { apiClient } from './api'
 *   const result = await apiClient.valuate({ asset_category, description, ... })
 */

// ── 后端地址（开发时由 Vite 代理，生产时从环境变量读取）────────────
const BASE_URL = import.meta.env.PROD
  ? (import.meta.env.VITE_API_URL || '')   // 生产：空字符串 = 同源，或填具体域名
  : '';                                     // 开发：Vite proxy 拦截 /api/*

// ── 默认超时（ms）──────────────────────────────────────────────────
const DEFAULT_TIMEOUT_MS = 8000;

// ── 结构化错误类型 ─────────────────────────────────────────────────
export class ApiError extends Error {
  constructor(message, type = 'UNKNOWN', status = null) {
    super(message);
    this.name  = 'ApiError';
    this.type  = type;   // 'TIMEOUT' | 'NETWORK' | 'SERVER' | 'REJECTED'
    this.status = status;
  }
}

// ── 核心 fetch 包装（统一超时 + 错误分类）─────────────────────────
async function apiFetch(path, options = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    });
    clearTimeout(timer);

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
import { useState, useEffect, useCallback } from 'react';

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

export function useWsValuate() {
  const [progress,     setProgress]     = useState([]);
  const [result,       setResult]       = useState(null);
  const [error,        setError]        = useState(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const wsRef = useCallback(null, []);

  const connect = useCallback((payload) => {
    setProgress([]);
    setResult(null);
    setError(null);
    setIsConnecting(true);

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // 开发时 Vite devServer 代理 /ws/* → ws://localhost:8000/ws/*
    const wsUrl = import.meta.env.PROD
      ? `${protocol}//${window.location.host}/ws/valuate`
      : `${protocol}//localhost:8000/ws/valuate`;

    const ws = new WebSocket(wsUrl);

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
        } else if (msg.type === 'error') {
          setError(msg.detail || '未知错误');
          setIsConnecting(false);
        }
      } catch (_) {}
    };

    ws.onerror = () => {
      setError('WebSocket 连接失败，请检查后端服务');
      setIsConnecting(false);
    };

    ws.onclose = () => {
      setIsConnecting(false);
    };

    return () => ws.close();
  }, []);

  return { connect, progress, result, error, isConnecting };
}
