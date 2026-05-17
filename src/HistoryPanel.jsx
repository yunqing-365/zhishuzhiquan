import React, { useState, useEffect, useCallback } from 'react';
import { Clock, Database, TrendingUp, RefreshCw, ChevronRight, Mic, FileText, Image as ImageIcon, Film, X } from 'lucide-react';

// ── 模态图标 & 颜色 ────────────────────────────────────────────────
const MODALITY_META = {
  text:  { icon: FileText, color: 'text-blue-400',    bg: 'bg-blue-900/20 border-blue-500/30',    label: '文本' },
  image: { icon: ImageIcon, color: 'text-amber-400',  bg: 'bg-amber-900/20 border-amber-500/30',  label: '图像' },
  audio: { icon: Mic,       color: 'text-emerald-400', bg: 'bg-emerald-900/20 border-emerald-500/30', label: '音频' },
  video: { icon: Film,      color: 'text-purple-400',  bg: 'bg-purple-900/20 border-purple-500/30',  label: '视频' },
};

const fmt = (n) => Number(n || 0).toLocaleString();
const fmtTime = (ts) => {
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

// ── 单条记录卡片 ──────────────────────────────────────────────────
const HistoryCard = ({ record, onClick }) => {
  const meta = MODALITY_META[record.modality] || MODALITY_META.text;
  const Icon = meta.icon;
  const price = record.dynamic_price || 0;

  return (
    <button
      onClick={() => onClick(record)}
      className="w-full text-left bg-slate-900/60 border border-slate-800 hover:border-slate-600 rounded-xl p-3.5 transition-all group"
    >
      <div className="flex items-start gap-3">
        {/* 模态图标 */}
        <div className={`shrink-0 mt-0.5 p-1.5 rounded-lg border ${meta.bg}`}>
          <Icon className={`w-3.5 h-3.5 ${meta.color}`} />
        </div>

        {/* 内容 */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className={`text-[10px] font-bold font-mono px-1.5 py-0.5 rounded border ${meta.bg} ${meta.color}`}>
              {meta.label}
            </span>
            {record.audio_scene && (
              <span className="text-[9px] text-slate-500 font-mono">{record.audio_scene}</span>
            )}
            <span className="text-[10px] text-slate-600 ml-auto">{fmtTime(record.timestamp)}</span>
          </div>

          <p className="text-xs text-slate-400 truncate leading-relaxed mb-2">
            {record.description_preview || '—'}
          </p>

          <div className="flex items-center gap-3 text-[10px] font-mono">
            <span className="text-slate-500">
              质量 <span className="text-slate-300">{record.composite_quality?.toFixed(1)}</span>
            </span>
            <span className="text-slate-500">
              场景 <span className="text-slate-300">{record.scene || '—'}</span>
            </span>
            <span className="text-emerald-400 ml-auto font-bold">
              {fmt(price)} CRD
            </span>
          </div>
        </div>

        <ChevronRight className="shrink-0 w-3.5 h-3.5 text-slate-700 group-hover:text-slate-400 mt-2 transition-colors" />
      </div>
    </button>
  );
};

// ── 统计汇总卡 ────────────────────────────────────────────────────
const StatBar = ({ stats, corpusSize }) => (
  <div className="grid grid-cols-3 gap-2 mb-4">
    <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-3 text-center">
      <p className="text-xs text-slate-500 mb-1">累计估值</p>
      <p className="text-lg font-mono text-white">{stats.total_valuations ?? 0}</p>
    </div>
    <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-3 text-center">
      <p className="text-xs text-slate-500 mb-1">向量知识库</p>
      <p className="text-lg font-mono text-purple-400">{corpusSize ?? 0}</p>
    </div>
    <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-3 text-center">
      <p className="text-xs text-slate-500 mb-1">均价 (CRD)</p>
      <p className="text-lg font-mono text-emerald-400">
        {fmt(stats.avg_dynamic_price)}
      </p>
    </div>
  </div>
);

// ── 主组件 ────────────────────────────────────────────────────────
export default function HistoryPanel({ isOpen, onClose }) {
  const [records, setRecords]   = useState([]);
  const [stats, setStats]       = useState({});
  const [corpusSize, setCorpus] = useState(0);
  const [loading, setLoading]   = useState(false);
  const [filter, setFilter]     = useState('');  // '' | 'text' | 'image' | 'audio' | 'video'
  const [error, setError]       = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [histRes, healthRes] = await Promise.all([
        fetch(`/api/history?limit=30${filter ? `&modality=${filter}` : ''}`),
        fetch('/api/health'),
      ]);
      if (histRes.ok) {
        const d = await histRes.json();
        setRecords(d.records || []);
      }
      if (healthRes.ok) {
        const h = await healthRes.json();
        setStats(h.db_stats || {});
        setCorpus(h.corpus_size || 0);
      }
    } catch (e) {
      setError('后端连接失败，请确认服务已启动');
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    if (isOpen) load();
  }, [isOpen, load]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* 背景遮罩 */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* 面板 */}
      <div className="relative w-full max-w-xl mx-4 bg-slate-950 border border-slate-700/60 rounded-2xl shadow-2xl flex flex-col max-h-[80vh]">

        {/* 头部 */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <Database className="w-4 h-4 text-purple-400" />
            <span className="font-bold text-sm text-white">估值历史</span>
            <span className="text-[10px] font-mono text-slate-500 bg-slate-800 px-1.5 py-0.5 rounded">
              SQLite + ChromaDB
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={load}
              disabled={loading}
              className="p-1.5 rounded-lg text-slate-500 hover:text-slate-300 hover:bg-slate-800 transition-all"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-slate-500 hover:text-slate-300 hover:bg-slate-800 transition-all"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* 统计栏 */}
        <div className="px-5 pt-4">
          <StatBar stats={stats} corpusSize={corpusSize} />

          {/* 模态过滤器 */}
          <div className="flex gap-1.5 mb-3">
            {['', 'text', 'image', 'audio', 'video'].map((m) => {
              const meta = m ? MODALITY_META[m] : null;
              const active = filter === m;
              return (
                <button
                  key={m}
                  onClick={() => setFilter(m)}
                  className={`text-[10px] font-mono px-2.5 py-1 rounded-lg border transition-all ${
                    active
                      ? 'border-purple-500/50 bg-purple-900/20 text-purple-400'
                      : 'border-slate-700 text-slate-500 hover:text-slate-300'
                  }`}
                >
                  {m ? meta?.label : '全部'}
                </button>
              );
            })}
          </div>
        </div>

        {/* 列表 */}
        <div className="flex-1 overflow-y-auto px-5 pb-5 space-y-2">
          {error && (
            <div className="text-center py-8 text-amber-400 text-sm">{error}</div>
          )}
          {!error && !loading && records.length === 0 && (
            <div className="text-center py-12">
              <Clock className="w-8 h-8 text-slate-700 mx-auto mb-3" />
              <p className="text-slate-500 text-sm">暂无估值记录</p>
              <p className="text-slate-600 text-xs mt-1">完成一次资产估值后，记录将自动保存至此</p>
            </div>
          )}
          {records.map((r) => (
            <HistoryCard
              key={r.id}
              record={r}
              onClick={() => {/* 可扩展：点击展开详情 */}}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
