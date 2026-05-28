/**
 * HumanReviewPanel.jsx — 人工复核管理面板
 * ==========================================
 * 第 15 轮新增：后端 /api/review/* 端点早在第 3 轮就已完备，
 * 但前端一直没有对应 UI。本组件补全这一缺口。
 *
 * 功能：
 *  - 列出待复核样本队列（自动 10s 轮询）
 *  - 展示每条样本的内容摘要、类型、质量分、拒绝原因
 *  - 一键批准 / 拒绝，支持批量操作
 *  - 已处理记录折叠查看
 */
import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  CheckCircle, XCircle, Clock, AlertTriangle,
  RefreshCw, ChevronDown, ChevronUp, Eye, EyeOff,
  Filter, Inbox, Shield, Zap
} from 'lucide-react';
import { apiFetch } from './api';

// ── API helpers ────────────────────────────────────────────────────
const reviewApi = {
  async list() {
    const res = await apiFetch('/api/review/queue');
    return res;
  },
  async approve(reviewId, reviewer = 'admin') {
    return apiFetch(`/api/review/${reviewId}/approve?reviewer=${encodeURIComponent(reviewer)}`, {
      method: 'POST',
    });
  },
  async reject(reviewId, reviewer = 'admin') {
    return apiFetch(`/api/review/${reviewId}/reject?reviewer=${encodeURIComponent(reviewer)}`, {
      method: 'POST',
    });
  },
};

// ── 样本类型标签 ───────────────────────────────────────────────────
const TYPE_BADGE = {
  sft:       { label: 'SFT',      bg: 'bg-purple-900/60',  text: 'text-purple-300',  border: 'border-purple-700/40' },
  dpo:       { label: 'DPO',      bg: 'bg-blue-900/60',    text: 'text-blue-300',    border: 'border-blue-700/40'   },
  pretrain:  { label: 'PT',       bg: 'bg-emerald-900/60', text: 'text-emerald-300', border: 'border-emerald-700/40'},
  multimodal:{ label: 'Multi',    bg: 'bg-amber-900/60',   text: 'text-amber-300',   border: 'border-amber-700/40'  },
};

const STATUS_STYLE = {
  pending:  { icon: Clock,       color: 'text-yellow-400', label: '待复核' },
  approved: { icon: CheckCircle, color: 'text-emerald-400', label: '已批准' },
  rejected: { icon: XCircle,     color: 'text-red-400',    label: '已拒绝' },
};

// ── 评分颜色 ───────────────────────────────────────────────────────
function scoreColor(score) {
  if (score >= 8)  return 'text-emerald-400';
  if (score >= 6)  return 'text-yellow-400';
  if (score >= 4)  return 'text-orange-400';
  return 'text-red-400';
}

// ── 单条复核卡片 ───────────────────────────────────────────────────
function ReviewCard({ item, reviewer, onDecision }) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading]   = useState(null); // 'approve' | 'reject' | null
  const typeMeta  = TYPE_BADGE[item.sample_type] || TYPE_BADGE.sft;
  const statusMeta = STATUS_STYLE[item.status] || STATUS_STYLE.pending;
  const StatusIcon = statusMeta.icon;

  const content = (() => {
    try { return typeof item.content === 'string' ? JSON.parse(item.content) : item.content; }
    catch { return {}; }
  })();

  const preview = content.instruction || content.prompt || content.text
    || content.chosen || JSON.stringify(content).slice(0, 120);

  async function handleDecision(decision) {
    setLoading(decision);
    try {
      if (decision === 'approve') {
        await reviewApi.approve(item.review_id, reviewer);
      } else {
        await reviewApi.reject(item.review_id, reviewer);
      }
      onDecision(item.review_id, decision);
    } catch (e) {
      console.error('复核操作失败:', e);
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className={`bg-slate-900 border rounded-xl overflow-hidden transition-all ${
      item.status === 'pending' ? 'border-slate-700' : 'border-slate-800 opacity-70'
    }`}>
      {/* 卡头 */}
      <div className="flex items-start gap-3 px-4 py-3">
        {/* 类型标签 */}
        <span className={`shrink-0 mt-0.5 px-2 py-0.5 rounded text-[10px] font-bold border ${typeMeta.bg} ${typeMeta.text} ${typeMeta.border}`}>
          {typeMeta.label}
        </span>

        {/* 内容预览 */}
        <div className="flex-1 min-w-0">
          <p className="text-slate-200 text-sm leading-snug line-clamp-2">
            {preview || '（无内容预览）'}
          </p>
          <div className="flex items-center gap-3 mt-1.5 text-[11px] text-slate-500">
            <span>质量分 <span className={`font-bold ${scoreColor(item.score)}`}>{item.score?.toFixed(1) ?? '—'}</span></span>
            <span>ID <span className="font-mono">{item.review_id.slice(0, 8)}…</span></span>
            <span>{new Date(item.created_at).toLocaleString('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' })}</span>
          </div>
          {item.reason && (
            <p className="mt-1 text-[11px] text-orange-400 flex items-center gap-1">
              <AlertTriangle size={10} />
              {item.reason}
            </p>
          )}
        </div>

        {/* 右侧状态 + 展开 */}
        <div className="flex flex-col items-end gap-2 shrink-0">
          <div className={`flex items-center gap-1 text-[11px] font-medium ${statusMeta.color}`}>
            <StatusIcon size={12} />
            {statusMeta.label}
          </div>
          <button
            onClick={() => setExpanded(v => !v)}
            className="text-slate-600 hover:text-slate-400 transition-colors"
          >
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        </div>
      </div>

      {/* 展开详情 */}
      {expanded && (
        <div className="border-t border-slate-800 px-4 py-3 bg-slate-950/40">
          <pre className="text-[11px] text-slate-400 font-mono whitespace-pre-wrap max-h-48 overflow-y-auto leading-relaxed">
            {JSON.stringify(content, null, 2)}
          </pre>
        </div>
      )}

      {/* 操作按钮（仅 pending 状态） */}
      {item.status === 'pending' && (
        <div className="flex items-center gap-2 px-4 py-2.5 border-t border-slate-800 bg-slate-950/20">
          <button
            onClick={() => handleDecision('approve')}
            disabled={!!loading}
            className="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg bg-emerald-600/20 hover:bg-emerald-600/40 border border-emerald-700/40 text-emerald-300 text-xs font-medium transition-all disabled:opacity-50"
          >
            {loading === 'approve'
              ? <RefreshCw size={12} className="animate-spin" />
              : <CheckCircle size={12} />}
            批准
          </button>
          <button
            onClick={() => handleDecision('reject')}
            disabled={!!loading}
            className="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg bg-red-600/20 hover:bg-red-600/40 border border-red-700/40 text-red-300 text-xs font-medium transition-all disabled:opacity-50"
          >
            {loading === 'reject'
              ? <RefreshCw size={12} className="animate-spin" />
              : <XCircle size={12} />}
            拒绝
          </button>
        </div>
      )}
    </div>
  );
}

// ── 主组件 ────────────────────────────────────────────────────────
export default function HumanReviewPanel({ reviewer = 'admin' }) {
  const [items, setItems]             = useState([]);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState(null);
  const [showResolved, setShowResolved] = useState(false);
  const [filterType, setFilterType]   = useState('all');
  const [batchSelected, setBatchSelected] = useState(new Set());
  const [batchLoading, setBatchLoading]   = useState(false);
  const intervalRef = useRef(null);

  // ── 拉取队列 ───────────────────────────────────────────────────
  const fetchQueue = useCallback(async () => {
    try {
      const data = await reviewApi.list();
      setItems(Array.isArray(data) ? data : (data.items ?? []));
      setError(null);
    } catch (e) {
      setError(e.message || '加载复核队列失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchQueue();
    intervalRef.current = setInterval(fetchQueue, 10_000);
    return () => clearInterval(intervalRef.current);
  }, [fetchQueue]);

  // ── 单条决策回调（乐观更新）───────────────────────────────────
  function handleDecision(reviewId, decision) {
    setItems(prev => prev.map(it =>
      it.review_id === reviewId
        ? { ...it, status: decision === 'approve' ? 'approved' : 'rejected', reviewer }
        : it
    ));
    setBatchSelected(prev => { const s = new Set(prev); s.delete(reviewId); return s; });
  }

  // ── 批量操作 ───────────────────────────────────────────────────
  async function handleBatch(decision) {
    setBatchLoading(true);
    const ids = [...batchSelected];
    await Promise.allSettled(ids.map(id =>
      decision === 'approve' ? reviewApi.approve(id, reviewer) : reviewApi.reject(id, reviewer)
    ));
    ids.forEach(id => handleDecision(id, decision));
    setBatchLoading(false);
    setBatchSelected(new Set());
  }

  // ── 过滤 ───────────────────────────────────────────────────────
  const pending  = items.filter(i => i.status === 'pending');
  const resolved = items.filter(i => i.status !== 'pending');

  function applyFilter(list) {
    if (filterType === 'all') return list;
    return list.filter(i => i.sample_type === filterType);
  }

  const visiblePending  = applyFilter(pending);
  const visibleResolved = applyFilter(resolved);

  // ── 批量选择 ──────────────────────────────────────────────────
  function toggleSelect(reviewId) {
    setBatchSelected(prev => {
      const s = new Set(prev);
      s.has(reviewId) ? s.delete(reviewId) : s.add(reviewId);
      return s;
    });
  }

  function selectAll() {
    if (batchSelected.size === visiblePending.length) {
      setBatchSelected(new Set());
    } else {
      setBatchSelected(new Set(visiblePending.map(i => i.review_id)));
    }
  }

  return (
    <div className="min-h-screen bg-slate-950 text-white px-4 py-8">
      <div className="max-w-3xl mx-auto space-y-6">

        {/* 头部 */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-white flex items-center gap-2">
              <Shield size={20} className="text-amber-400" />
              人工复核队列
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">
              低质量 / 内容安全拦截的样本，由人工审核决定是否录用
            </p>
          </div>
          <button
            onClick={fetchQueue}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700 text-slate-400 hover:text-slate-200 text-xs transition-all"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>

        {/* 统计卡 */}
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: '待复核', value: pending.length,  color: 'text-yellow-400', icon: Clock },
            { label: '已批准', value: resolved.filter(i=>i.status==='approved').length, color: 'text-emerald-400', icon: CheckCircle },
            { label: '已拒绝', value: resolved.filter(i=>i.status==='rejected').length, color: 'text-red-400',     icon: XCircle },
          ].map(({ label, value, color, icon: Icon }) => (
            <div key={label} className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-3 flex items-center gap-3">
              <Icon size={18} className={color} />
              <div>
                <p className={`text-lg font-bold ${color}`}>{value}</p>
                <p className="text-[11px] text-slate-500">{label}</p>
              </div>
            </div>
          ))}
        </div>

        {/* 筛选栏 + 批量操作 */}
        <div className="flex items-center gap-3 flex-wrap">
          {/* 类型筛选 */}
          <div className="flex items-center gap-1 bg-slate-900 border border-slate-800 rounded-lg px-2 py-1">
            <Filter size={12} className="text-slate-500" />
            {['all', 'sft', 'dpo', 'pretrain', 'multimodal'].map(t => (
              <button
                key={t}
                onClick={() => setFilterType(t)}
                className={`px-2 py-0.5 rounded text-[11px] font-medium transition-all ${
                  filterType === t
                    ? 'bg-slate-700 text-white'
                    : 'text-slate-500 hover:text-slate-300'
                }`}
              >
                {t === 'all' ? '全部' : t.toUpperCase()}
              </button>
            ))}
          </div>

          {/* 批量操作（有待复核时展示） */}
          {visiblePending.length > 0 && (
            <div className="flex items-center gap-2 ml-auto">
              <button
                onClick={selectAll}
                className="text-[11px] text-slate-400 hover:text-slate-200 underline transition-colors"
              >
                {batchSelected.size === visiblePending.length ? '取消全选' : `全选(${visiblePending.length})`}
              </button>
              {batchSelected.size > 0 && (
                <>
                  <span className="text-slate-700">|</span>
                  <button
                    onClick={() => handleBatch('approve')}
                    disabled={batchLoading}
                    className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-emerald-600/20 hover:bg-emerald-600/40 border border-emerald-700/40 text-emerald-300 text-[11px] font-medium transition-all disabled:opacity-50"
                  >
                    <Zap size={10} />
                    批量批准({batchSelected.size})
                  </button>
                  <button
                    onClick={() => handleBatch('reject')}
                    disabled={batchLoading}
                    className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-red-600/20 hover:bg-red-600/40 border border-red-700/40 text-red-300 text-[11px] font-medium transition-all disabled:opacity-50"
                  >
                    <XCircle size={10} />
                    批量拒绝({batchSelected.size})
                  </button>
                </>
              )}
            </div>
          )}
        </div>

        {/* 错误状态 */}
        {error && (
          <div className="bg-red-900/20 border border-red-800/40 rounded-xl px-4 py-3 text-red-400 text-sm flex items-center gap-2">
            <AlertTriangle size={14} />
            {error}
          </div>
        )}

        {/* 待复核列表 */}
        {loading ? (
          <div className="space-y-3">
            {[...Array(3)].map((_, i) => (
              <div key={i} className="bg-slate-900 border border-slate-800 rounded-xl h-24 animate-pulse" />
            ))}
          </div>
        ) : visiblePending.length === 0 ? (
          <div className="text-center py-16">
            <Inbox size={36} className="mx-auto text-slate-700 mb-3" />
            <p className="text-slate-500 text-sm">暂无待复核样本</p>
            <p className="text-slate-700 text-xs mt-1">所有样本已处理完毕，或流水线尚未触发人工复核</p>
          </div>
        ) : (
          <div className="space-y-3">
            {visiblePending.map(item => (
              <div key={item.review_id} className="relative">
                {/* 批量选择勾选框 */}
                <input
                  type="checkbox"
                  checked={batchSelected.has(item.review_id)}
                  onChange={() => toggleSelect(item.review_id)}
                  className="absolute top-4 right-4 z-10 w-3.5 h-3.5 accent-purple-500 cursor-pointer"
                />
                <ReviewCard
                  item={item}
                  reviewer={reviewer}
                  onDecision={handleDecision}
                />
              </div>
            ))}
          </div>
        )}

        {/* 已处理折叠区 */}
        {visibleResolved.length > 0 && (
          <div>
            <button
              onClick={() => setShowResolved(v => !v)}
              className="flex items-center gap-2 text-slate-500 hover:text-slate-300 text-sm transition-colors w-full py-2"
            >
              {showResolved ? <EyeOff size={14} /> : <Eye size={14} />}
              {showResolved ? '收起' : '展开'}已处理记录 ({visibleResolved.length} 条)
            </button>
            {showResolved && (
              <div className="space-y-3 mt-3">
                {visibleResolved.map(item => (
                  <ReviewCard
                    key={item.review_id}
                    item={item}
                    reviewer={reviewer}
                    onDecision={handleDecision}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
