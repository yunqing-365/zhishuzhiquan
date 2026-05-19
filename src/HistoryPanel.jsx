/**
 * HistoryPanel — 估值历史面板 v2
 * ===============================
 * v1 → v2 升级:
 *   [核心] 接入 /api/stats 端点，StatBar 展示真实 by_modality 分布 + 平均质量分
 *   [新增] 搜索框：调用 apiClient.historySearch()，实时模糊搜索描述/场景/hash
 *   [新增] 记录删除：每条卡片悬停出现删除按钮，调用 apiClient.deleteHistory()
 *   [新增] Top-N 排行标签页：调用 apiClient.topAssets()，显示高价值资产排行
 *   [改进] StatBar 增加 by_modality 分布迷你柱状图（纯 CSS，无需 recharts）
 *   [改进] 错误状态细化：区分搜索无结果 vs 网络错误 vs 空库
 *   [兼容] 若后端版本低于 v2（无 /api/stats），StatBar 静默降级到 v1 health 数据
 */
import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Clock, Database, TrendingUp, RefreshCw, ChevronRight,
  Mic, FileText, Image as ImageIcon, Film, X, Search,
  Trash2, Trophy, AlertCircle,
} from 'lucide-react';
import { apiClient } from './api';

// ── 模态元数据 ────────────────────────────────────────────────────
const MODALITY_META = {
  text:  { icon: FileText,  color: 'text-blue-400',    bg: 'bg-blue-900/20 border-blue-500/30',    label: '文本' },
  image: { icon: ImageIcon, color: 'text-amber-400',   bg: 'bg-amber-900/20 border-amber-500/30',  label: '图像' },
  audio: { icon: Mic,       color: 'text-emerald-400', bg: 'bg-emerald-900/20 border-emerald-500/30', label: '音频' },
  video: { icon: Film,      color: 'text-purple-400',  bg: 'bg-purple-900/20 border-purple-500/30',  label: '视频' },
};

const fmt     = (n) => Number(n || 0).toLocaleString();
const fmtTime = (ts) => {
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

// ── 单条记录卡片（v2：增加删除按钮）──────────────────────────────
const HistoryCard = ({ record, onClick, onDelete }) => {
  const [hovered, setHovered] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const meta = MODALITY_META[record.modality] || MODALITY_META.text;
  const Icon = meta.icon;

  const handleDelete = async (e) => {
    e.stopPropagation();
    if (!window.confirm('删除这条估值记录？')) return;
    setDeleting(true);
    try {
      await onDelete(record.id);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div
      className="relative group"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        onClick={() => onClick(record)}
        className="w-full text-left bg-slate-900/60 border border-slate-800 hover:border-slate-600 rounded-xl p-3.5 transition-all"
      >
        <div className="flex items-start gap-3">
          <div className={`shrink-0 mt-0.5 p-1.5 rounded-lg border ${meta.bg}`}>
            <Icon className={`w-3.5 h-3.5 ${meta.color}`} />
          </div>
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
                {fmt(record.dynamic_price)} CRD
              </span>
            </div>
          </div>
          <ChevronRight className="shrink-0 w-3.5 h-3.5 text-slate-700 group-hover:text-slate-400 mt-2 transition-colors" />
        </div>
      </button>

      {/* 删除按钮（悬停时出现）*/}
      {hovered && (
        <button
          onClick={handleDelete}
          disabled={deleting}
          className="absolute top-2 right-8 p-1 rounded-lg bg-red-950/60 border border-red-500/30 text-red-400 hover:bg-red-900/60 transition-all opacity-0 group-hover:opacity-100"
          title="删除此记录"
        >
          <Trash2 className={`w-3 h-3 ${deleting ? 'animate-pulse' : ''}`} />
        </button>
      )}
    </div>
  );
};

// ── 统计栏 v2（接入真实 /api/stats）──────────────────────────────
const StatBar = ({ stats, detailedStats, corpusSize }) => {
  // 优先使用 /api/stats 的详细数据，否则降级到 /api/health 的 db_stats
  const total   = detailedStats?.total   ?? stats.total_valuations ?? 0;
  const avgQ    = detailedStats?.avg_quality ?? null;
  const byMod   = detailedStats?.by_modality ?? {};
  const modKeys = Object.keys(byMod);

  // 模态分布最大值（用于柱状图归一化）
  const maxCount = Math.max(...modKeys.map(k => byMod[k].count), 1);

  return (
    <div className="mb-4 space-y-2">
      {/* 顶部三格统计 */}
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-3 text-center">
          <p className="text-xs text-slate-500 mb-1">累计估值</p>
          <p className="text-lg font-mono text-white">{total}</p>
        </div>
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-3 text-center">
          <p className="text-xs text-slate-500 mb-1">向量知识库</p>
          <p className="text-lg font-mono text-purple-400">{corpusSize ?? 0}</p>
        </div>
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-3 text-center">
          <p className="text-xs text-slate-500 mb-1">{avgQ != null ? '平均质量分' : '均价 (CRD)'}</p>
          <p className="text-lg font-mono text-emerald-400">
            {avgQ != null ? avgQ.toFixed(1) : fmt(stats.avg_dynamic_price)}
          </p>
        </div>
      </div>

      {/* 模态分布迷你柱状图（仅在 /api/stats 有数据时渲染）*/}
      {modKeys.length > 0 && (
        <div className="bg-slate-900/40 border border-slate-800 rounded-xl px-4 py-3">
          <p className="text-[9px] text-slate-600 uppercase tracking-widest mb-2">模态分布</p>
          <div className="flex items-end gap-3">
            {modKeys.map((k) => {
              const meta = MODALITY_META[k] || MODALITY_META.text;
              const Icon = meta.icon;
              const barH = Math.max(8, Math.round((byMod[k].count / maxCount) * 40));
              return (
                <div key={k} className="flex flex-col items-center gap-1 flex-1">
                  <span className="text-[9px] font-mono text-slate-500">{byMod[k].count}</span>
                  <div
                    className={`w-full rounded-sm transition-all ${
                      k === 'text'  ? 'bg-blue-500/50' :
                      k === 'image' ? 'bg-amber-500/50' :
                      k === 'audio' ? 'bg-emerald-500/50' : 'bg-purple-500/50'
                    }`}
                    style={{ height: `${barH}px` }}
                  />
                  <Icon className={`w-3 h-3 ${meta.color}`} />
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

// ── Top 资产排行榜 ────────────────────────────────────────────────
const TopAssetsList = ({ assets, loading }) => {
  if (loading) {
    return <div className="text-center py-8 text-slate-600 text-sm animate-pulse">加载排行榜...</div>;
  }
  if (!assets.length) {
    return (
      <div className="text-center py-12">
        <Trophy className="w-8 h-8 text-slate-700 mx-auto mb-3" />
        <p className="text-slate-500 text-sm">暂无排行数据</p>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {assets.map((a, i) => {
        const meta = MODALITY_META[a.modality] || MODALITY_META.text;
        const Icon = meta.icon;
        const rankColors = ['text-amber-400', 'text-slate-300', 'text-amber-700'];
        return (
          <div key={a.id} className="flex items-center gap-3 bg-slate-900/60 border border-slate-800 rounded-xl p-3">
            <span className={`text-sm font-bold font-mono w-5 text-center ${rankColors[i] || 'text-slate-600'}`}>
              {i + 1}
            </span>
            <div className={`p-1.5 rounded-lg border ${meta.bg}`}>
              <Icon className={`w-3 h-3 ${meta.color}`} />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-[11px] text-slate-400 truncate">{a.description_preview || '—'}</p>
              <p className="text-[10px] text-slate-600 font-mono">{a.scene || '—'}</p>
            </div>
            <span className="text-emerald-400 text-sm font-mono font-bold shrink-0">
              {fmt(a.dynamic_price)} CRD
            </span>
          </div>
        );
      })}
    </div>
  );
};

// ── 主组件 ────────────────────────────────────────────────────────
export default function HistoryPanel({ isOpen, onClose }) {
  const [activeTab,     setActiveTab]     = useState('history');  // 'history' | 'top'
  const [records,       setRecords]       = useState([]);
  const [topAssets,     setTopAssets]     = useState([]);
  const [stats,         setStats]         = useState({});
  const [detailedStats, setDetailedStats] = useState(null);
  const [corpusSize,    setCorpus]        = useState(0);
  const [loading,       setLoading]       = useState(false);
  const [topLoading,    setTopLoading]    = useState(false);
  const [filter,        setFilter]        = useState('');   // '' | 'text' | 'image' | 'audio'
  const [searchQ,       setSearchQ]       = useState('');
  const [error,         setError]         = useState(null);
  const searchTimer = useRef(null);

  // ── 加载历史记录 ─────────────────────────────────────────────
  const load = useCallback(async (q = '') => {
    setLoading(true);
    setError(null);
    try {
      // 同时请求历史、health、stats（三路并发）
      const [histRes, healthRes, statsRes] = await Promise.allSettled([
        q.trim()
          ? apiClient.historySearch(q.trim(), 30)
          : apiClient.history(30, filter),
        apiClient.health(),
        apiClient.stats(),   // v2 新增端点
      ]);

      if (histRes.status === 'fulfilled') {
        setRecords(histRes.value.records || []);
      }
      if (healthRes.status === 'fulfilled') {
        setStats(healthRes.value.db_stats || {});
        setCorpus(healthRes.value.corpus_size || 0);
      }
      if (statsRes.status === 'fulfilled') {
        setDetailedStats(statsRes.value.stats || null);
        // corpus_size 以 /api/stats 为准（更准）
        if (statsRes.value.corpus_size != null) {
          setCorpus(statsRes.value.corpus_size);
        }
      }
    } catch (e) {
      setError('后端连接失败，请确认服务已启动');
    } finally {
      setLoading(false);
    }
  }, [filter]);

  // ── 加载排行榜 ───────────────────────────────────────────────
  const loadTop = useCallback(async () => {
    setTopLoading(true);
    try {
      const res = await apiClient.topAssets(10, filter);
      setTopAssets(res.assets || []);
    } catch {
      setTopAssets([]);
    } finally {
      setTopLoading(false);
    }
  }, [filter]);

  // ── 搜索防抖（300ms）────────────────────────────────────────
  const handleSearch = (q) => {
    setSearchQ(q);
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => load(q), 300);
  };

  // ── 删除记录 ────────────────────────────────────────────────
  const handleDelete = useCallback(async (id) => {
    try {
      await apiClient.deleteHistory(id);
      setRecords(prev => prev.filter(r => r.id !== id));
      // 更新 total 统计
      setDetailedStats(prev => prev ? { ...prev, total: Math.max(0, (prev.total || 1) - 1) } : prev);
    } catch (e) {
      alert('删除失败: ' + e.message);
    }
  }, []);

  // ── 面板打开时加载数据 ──────────────────────────────────────
  useEffect(() => {
    if (!isOpen) return;
    if (activeTab === 'history') load(searchQ);
    else loadTop();
  }, [isOpen, activeTab, filter]);   // searchQ 由防抖控制，不在依赖里

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* 背景遮罩 */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      {/* 面板 */}
      <div className="relative w-full max-w-xl mx-4 bg-slate-950 border border-slate-700/60 rounded-2xl shadow-2xl flex flex-col max-h-[85vh]">

        {/* 头部 */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <Database className="w-4 h-4 text-purple-400" />
            <span className="font-bold text-sm text-white">数据资产中心</span>
            <span className="text-[10px] font-mono text-slate-500 bg-slate-800 px-1.5 py-0.5 rounded">
              SQLite · ChromaDB · v2
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => activeTab === 'history' ? load(searchQ) : loadTop()}
              disabled={loading || topLoading}
              className="p-1.5 rounded-lg text-slate-500 hover:text-slate-300 hover:bg-slate-800 transition-all"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${(loading || topLoading) ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-slate-500 hover:text-slate-300 hover:bg-slate-800 transition-all"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* 标签页 */}
        <div className="flex border-b border-slate-800 px-5">
          {[
            { id: 'history', label: '估值历史', icon: Clock },
            { id: 'top',     label: 'Top 资产', icon: Trophy },
          ].map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center gap-1.5 px-3 py-3 text-xs font-medium border-b-2 transition-all mr-2 ${
                activeTab === id
                  ? 'border-purple-500 text-purple-400'
                  : 'border-transparent text-slate-500 hover:text-slate-300'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
            </button>
          ))}
        </div>

        {/* 内容区 */}
        <div className="flex-1 overflow-y-auto flex flex-col">
          {activeTab === 'history' ? (
            <div className="px-5 pt-4 flex flex-col gap-3 flex-1">
              {/* 统计栏 */}
              <StatBar stats={stats} detailedStats={detailedStats} corpusSize={corpusSize} />

              {/* 搜索框（v2 新增）*/}
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-600" />
                <input
                  type="text"
                  value={searchQ}
                  onChange={(e) => handleSearch(e.target.value)}
                  placeholder="搜索描述、场景、哈希..."
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl pl-8 pr-3 py-2 text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-purple-500/50 transition-colors"
                />
                {searchQ && (
                  <button
                    onClick={() => { setSearchQ(''); load(''); }}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-600 hover:text-slate-400"
                  >
                    <X className="w-3 h-3" />
                  </button>
                )}
              </div>

              {/* 模态过滤器 */}
              <div className="flex gap-1.5">
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

              {/* 列表 */}
              <div className="flex-1 pb-5 space-y-2">
                {error && (
                  <div className="flex items-center gap-2 text-amber-400 text-xs p-3 bg-amber-950/20 border border-amber-500/20 rounded-xl">
                    <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                    {error}
                  </div>
                )}
                {!error && loading && (
                  <div className="text-center py-8 text-slate-600 text-sm animate-pulse">加载中...</div>
                )}
                {!error && !loading && records.length === 0 && (
                  <div className="text-center py-12">
                    <Clock className="w-8 h-8 text-slate-700 mx-auto mb-3" />
                    <p className="text-slate-500 text-sm">
                      {searchQ ? `"${searchQ}" 无匹配记录` : '暂无估值记录'}
                    </p>
                    {!searchQ && (
                      <p className="text-slate-600 text-xs mt-1">完成一次资产估值后，记录将自动保存至此</p>
                    )}
                  </div>
                )}
                {!loading && records.map((r) => (
                  <HistoryCard
                    key={r.id}
                    record={r}
                    onClick={() => {}}
                    onDelete={handleDelete}
                  />
                ))}
              </div>
            </div>
          ) : (
            <div className="px-5 pt-4 pb-5 flex-1">
              {/* 排行榜过滤 */}
              <div className="flex gap-1.5 mb-4">
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
              <TopAssetsList assets={topAssets} loading={topLoading} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
