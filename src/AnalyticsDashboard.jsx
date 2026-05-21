/**
 * AnalyticsDashboard.jsx — 数据资产市场看板 v1
 * =============================================
 * 接入 /api/stats + /api/history + /api/top 三个端点
 * 展示：总览卡片 · 价格走势 · 场景分布 · 模态占比 · 高价值排行
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Cell,
} from 'recharts';
import {
  X, TrendingUp, Database, Zap, Activity,
  BarChart2, FileText, Image as ImageIcon, Mic, Film, RefreshCw,
} from 'lucide-react';
import { apiClient } from './api';

// ── 模态元数据 ──────────────────────────────────────────────────────
const MODALITY_META = {
  text:  { hex: '#60a5fa', label: '文本', icon: FileText  },
  image: { hex: '#fbbf24', label: '图像', icon: ImageIcon },
  audio: { hex: '#34d399', label: '音频', icon: Mic       },
  video: { hex: '#a78bfa', label: '视频', icon: Film      },
};

// ── 场景中文映射 ────────────────────────────────────────────────────
const SCENE_LABELS = {
  medical_sft: '医疗 SFT', legal_doc: '法律文书', code_tech: '代码技术',
  creative: '创意写作', chat_qa: '问答对话', illustration: '原创插画',
  photo: '摄影作品', diagram: '图表图解', screenshot: '截图素材',
  speech_medical: '医疗语音', speech_legal: '法律音频', speech_edu: '教育语音',
  music_original: '原创音乐', ambient_sfx: '环境音效', general: '通用',
  documentary: '纪录/访谈', lecture: '教学讲解', cinematic: '影视创作',
  sports_action: '运动/动作', vlog: '个人 vlog', noise: '噪声',
};

// ── 自定义 Recharts Tooltip ─────────────────────────────────────────
const DarkTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-[11px] font-mono shadow-xl z-10">
      {label && <div className="text-slate-400 mb-1.5">{label}</div>}
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2" style={{ color: p.color || '#94a3b8' }}>
          <span className="w-2 h-2 rounded-full shrink-0" style={{ background: p.color || '#94a3b8' }} />
          {p.name}: <strong>{typeof p.value === 'number' ? p.value.toLocaleString() : p.value}</strong>
        </div>
      ))}
    </div>
  );
};

// ── 单个指标卡片 ────────────────────────────────────────────────────
const StatCard = ({ label, value, sub, color }) => (
  <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
    <div className="text-[10px] font-mono text-slate-500 mb-1 uppercase tracking-wider">{label}</div>
    <div className={`text-2xl font-bold font-mono ${color}`}>{value}</div>
    <div className="text-[10px] text-slate-600 mt-0.5">{sub}</div>
  </div>
);

// ── 主组件 ──────────────────────────────────────────────────────────
export default function AnalyticsDashboard({ isOpen, onClose }) {
  const [stats,   setStats]   = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);
  const [tab,     setTab]     = useState('overview');

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statsData, histData] = await Promise.all([
        apiClient.stats(),
        apiClient.history(60),
      ]);
      setStats(statsData);
      // 时间正序排列，用于走势图
      setHistory([...(histData.records || [])].reverse());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      setTab('overview');
      fetchData();
    }
  }, [isOpen, fetchData]);

  if (!isOpen) return null;

  // ── 衍生数据 ──────────────────────────────────────────────────────
  const modalityStats = stats?.stats?.by_modality || {};
  const total         = stats?.stats?.total        || 0;
  const avgQuality    = stats?.stats?.avg_quality  || 0;
  const corpusSize    = stats?.corpus_size         || 0;
  const topAssets     = stats?.top_assets          || [];

  const modalityList = Object.entries(modalityStats).map(([k, v]) => ({
    key: k, count: v.count || 0, avg_price: v.avg_price || 0, max_price: v.max_price || 0,
    ...MODALITY_META[k],
  }));

  const globalAvgPrice = total > 0
    ? Math.round(modalityList.reduce((s, d) => s + d.avg_price * d.count, 0) / total)
    : 0;

  // 价格 & 质量走势数据
  const trendData = history.map((r, i) => ({
    idx:     i + 1,
    time:    r.timestamp
               ? new Date(r.timestamp * 1000).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
               : `#${r.id}`,
    price:   Math.round(r.dynamic_price || 0),
    base:    Math.round(r.base_value    || 0),
    quality: Math.round((r.composite_quality || 0) * 100),
  }));

  // 场景分布
  const sceneData = (stats?.stats?.top_scenes || []).slice(0, 8).map(s => ({
    name:      SCENE_LABELS[s.scene] || s.scene,
    count:     s.count     || 0,
    avg_price: s.avg_price || 0,
  }));

  // ── 标签页定义 ────────────────────────────────────────────────────
  const TABS = [
    { id: 'overview', label: '总览',  icon: Activity   },
    { id: 'trends',   label: '走势',  icon: TrendingUp  },
    { id: 'market',   label: '市场',  icon: BarChart2   },
    { id: 'top',      label: '排行',  icon: Zap         },
  ];

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-slate-950/98 backdrop-blur-2xl overflow-hidden">

      {/* ── Header ────────────────────────────────────────────────── */}
      <div className="shrink-0 flex items-center justify-between px-6 py-4 border-b border-slate-800">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg bg-purple-500/20 border border-purple-500/40 flex items-center justify-center">
            <BarChart2 className="w-4 h-4 text-purple-400" />
          </div>
          <h2 className="text-sm font-bold text-white font-mono tracking-wider">数据资产看板</h2>
          <span className="text-[10px] font-mono text-slate-500 border border-slate-700 rounded px-1.5 py-0.5">
            AI-Echo v5
          </span>
          {!loading && !error && (
            <span className="text-[10px] font-mono text-emerald-500">
              · {total} 条记录
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={fetchData}
            disabled={loading}
            title="刷新数据"
            className="p-1.5 rounded-lg text-slate-500 hover:text-slate-300 hover:bg-slate-800 transition-all"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-500 hover:text-slate-300 hover:bg-slate-800 transition-all"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* ── Tabs ──────────────────────────────────────────────────── */}
      <div className="shrink-0 flex gap-1 px-6 pt-3 pb-2 border-b border-slate-800/50">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono transition-all ${
              tab === id
                ? 'bg-purple-500/20 text-purple-300 border border-purple-500/30'
                : 'text-slate-500 hover:text-slate-300 hover:bg-slate-800'
            }`}
          >
            <Icon className="w-3 h-3" />
            {label}
          </button>
        ))}
      </div>

      {/* ── Content ───────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto px-6 py-4">

        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center h-64">
            <div className="flex flex-col items-center gap-3 text-slate-500">
              <div className="w-8 h-8 border-2 border-purple-500/40 border-t-purple-400 rounded-full animate-spin" />
              <span className="text-xs font-mono">正在加载看板数据...</span>
            </div>
          </div>
        )}

        {/* Error */}
        {!loading && error && (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <div className="text-red-400 text-sm font-mono">⚠ 后端未连接</div>
            <div className="text-slate-600 text-xs font-mono max-w-xs text-center">{error}</div>
            <button onClick={fetchData} className="mt-2 px-4 py-1.5 text-xs font-mono border border-slate-700 rounded-lg text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-all">
              重试
            </button>
          </div>
        )}

        {/* Data */}
        {!loading && !error && (
          <>

            {/* ════ 总览 ════ */}
            {tab === 'overview' && (
              <div className="space-y-4">

                {/* Summary cards */}
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                  <StatCard label="总估值次数"  value={total.toLocaleString()} sub="历史累计"   color="text-blue-400"    />
                  <StatCard label="向量库规模"  value={corpusSize.toLocaleString()} sub="已入库资产" color="text-emerald-400" />
                  <StatCard label="平均质量分"  value={`${avgQuality}%`}  sub="综合评分" color="text-amber-400"  />
                  <StatCard label="全局均价"    value={`$${globalAvgPrice.toLocaleString()}`} sub="USDT" color="text-purple-400"  />
                </div>

                {/* Modality distribution */}
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                  <div className="text-xs font-mono text-slate-400 mb-4 flex items-center gap-2">
                    <Database className="w-3 h-3 text-blue-400" />
                    模态分布
                  </div>
                  {modalityList.length === 0 ? (
                    <div className="text-xs text-slate-600 py-6 text-center">暂无估值数据</div>
                  ) : (
                    <div className="space-y-3">
                      {modalityList.sort((a, b) => b.count - a.count).map(({ key, label, hex, icon: Icon, count, avg_price, max_price }) => {
                        const pct = total > 0 ? Math.round((count / total) * 100) : 0;
                        return (
                          <div key={key} className="flex items-center gap-3">
                            <Icon className="w-3.5 h-3.5 shrink-0" style={{ color: hex }} />
                            <div className="w-10 text-[11px] font-mono text-slate-400 shrink-0">{label}</div>
                            <div className="flex-1 h-1.5 bg-slate-800 rounded-full overflow-hidden">
                              <div
                                className="h-full rounded-full transition-all duration-700 ease-out"
                                style={{ width: `${pct}%`, background: hex }}
                              />
                            </div>
                            <div className="text-[11px] font-mono text-slate-500 w-8 text-right">{pct}%</div>
                            <div className="text-[11px] font-mono text-slate-600 w-24 text-right hidden sm:block">
                              均 <span style={{ color: hex }}>${avg_price.toLocaleString()}</span>
                            </div>
                            <div className="text-[11px] font-mono text-slate-700 w-20 text-right hidden md:block">
                              最高 ${Math.round(max_price).toLocaleString()}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* Mini price preview */}
                {trendData.length >= 4 && (
                  <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                    <div className="text-xs font-mono text-slate-400 mb-3 flex items-center gap-2">
                      <TrendingUp className="w-3 h-3 text-purple-400" />
                      最近价格预览
                    </div>
                    <ResponsiveContainer width="100%" height={100}>
                      <AreaChart data={trendData.slice(-20)} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                        <defs>
                          <linearGradient id="miniGrad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%"  stopColor="#a78bfa" stopOpacity={0.4} />
                            <stop offset="95%" stopColor="#a78bfa" stopOpacity={0}   />
                          </linearGradient>
                        </defs>
                        <Area type="monotone" dataKey="price" stroke="#a78bfa" strokeWidth={2} fill="url(#miniGrad)" dot={false} />
                        <Tooltip content={<DarkTooltip />} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </div>
            )}

            {/* ════ 走势 ════ */}
            {tab === 'trends' && (
              <div className="space-y-4">

                {/* Price trend */}
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                  <div className="text-xs font-mono text-slate-400 mb-4 flex items-center gap-2">
                    <TrendingUp className="w-3 h-3 text-purple-400" />
                    估值价格走势 — 最近 {trendData.length} 条
                  </div>
                  {trendData.length < 2 ? (
                    <div className="text-xs text-slate-600 py-12 text-center">至少需要 2 条记录才能显示走势图</div>
                  ) : (
                    <ResponsiveContainer width="100%" height={240}>
                      <AreaChart data={trendData} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
                        <defs>
                          <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%"  stopColor="#a78bfa" stopOpacity={0.35} />
                            <stop offset="95%" stopColor="#a78bfa" stopOpacity={0}    />
                          </linearGradient>
                          <linearGradient id="baseGrad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%"  stopColor="#60a5fa" stopOpacity={0.2} />
                            <stop offset="95%" stopColor="#60a5fa" stopOpacity={0}   />
                          </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                        <XAxis
                          dataKey="time"
                          tick={{ fill: '#475569', fontSize: 10, fontFamily: 'monospace' }}
                          tickLine={false} axisLine={false} interval="preserveStartEnd"
                        />
                        <YAxis
                          tick={{ fill: '#475569', fontSize: 10, fontFamily: 'monospace' }}
                          tickLine={false} axisLine={false}
                          tickFormatter={v => `$${(v / 1000).toFixed(1)}k`}
                        />
                        <Tooltip content={<DarkTooltip />} />
                        <Area type="monotone" dataKey="base"  name="基础估值" stroke="#60a5fa" strokeWidth={1.5} fill="url(#baseGrad)"  dot={false} strokeDasharray="4 2" />
                        <Area type="monotone" dataKey="price" name="动态报价" stroke="#a78bfa" strokeWidth={2}   fill="url(#priceGrad)" dot={false} />
                      </AreaChart>
                    </ResponsiveContainer>
                  )}
                  {trendData.length >= 2 && (
                    <div className="flex items-center gap-4 mt-3 text-[10px] font-mono text-slate-600">
                      <span className="flex items-center gap-1.5"><span className="w-6 h-px bg-blue-400/60 inline-block border-dashed border" />基础估值</span>
                      <span className="flex items-center gap-1.5"><span className="w-6 h-0.5 bg-purple-400 inline-block" />动态报价</span>
                    </div>
                  )}
                </div>

                {/* Quality trend */}
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                  <div className="text-xs font-mono text-slate-400 mb-4 flex items-center gap-2">
                    <Activity className="w-3 h-3 text-emerald-400" />
                    质量分走势
                  </div>
                  {trendData.length < 2 ? (
                    <div className="text-xs text-slate-600 py-8 text-center">暂无数据</div>
                  ) : (
                    <ResponsiveContainer width="100%" height={160}>
                      <AreaChart data={trendData} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
                        <defs>
                          <linearGradient id="qGrad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%"  stopColor="#34d399" stopOpacity={0.3} />
                            <stop offset="95%" stopColor="#34d399" stopOpacity={0}   />
                          </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                        <XAxis dataKey="time" tick={{ fill: '#475569', fontSize: 10 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
                        <YAxis tick={{ fill: '#475569', fontSize: 10 }} tickLine={false} axisLine={false} domain={[0, 100]} tickFormatter={v => `${v}%`} />
                        <Tooltip content={<DarkTooltip />} />
                        <Area type="monotone" dataKey="quality" name="质量分" stroke="#34d399" strokeWidth={2} fill="url(#qGrad)" dot={false} />
                      </AreaChart>
                    </ResponsiveContainer>
                  )}
                </div>
              </div>
            )}

            {/* ════ 市场 ════ */}
            {tab === 'market' && (
              <div className="space-y-4">

                {/* Scene count */}
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                  <div className="text-xs font-mono text-slate-400 mb-4 flex items-center gap-2">
                    <BarChart2 className="w-3 h-3 text-amber-400" />
                    热门场景 — 估值次数
                  </div>
                  {sceneData.length === 0 ? (
                    <div className="text-xs text-slate-600 py-8 text-center">暂无数据</div>
                  ) : (
                    <ResponsiveContainer width="100%" height={sceneData.length * 40 + 20}>
                      <BarChart data={sceneData} layout="vertical" margin={{ left: 8, right: 24, top: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" horizontal={false} />
                        <XAxis type="number" tick={{ fill: '#475569', fontSize: 10 }} tickLine={false} axisLine={false} />
                        <YAxis type="category" dataKey="name" tick={{ fill: '#94a3b8', fontSize: 11, fontFamily: 'monospace' }} tickLine={false} axisLine={false} width={76} />
                        <Tooltip content={<DarkTooltip />} />
                        <Bar dataKey="count" name="次数" radius={[0, 4, 4, 0]} maxBarSize={20}>
                          {sceneData.map((_, i) => (
                            <Cell key={i} fill={`hsl(${260 + i * 14}, 65%, ${58 - i * 3}%)`} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  )}
                </div>

                {/* Scene avg price */}
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                  <div className="text-xs font-mono text-slate-400 mb-4">场景均价对比 (USDT)</div>
                  {sceneData.length === 0 ? (
                    <div className="text-xs text-slate-600 py-8 text-center">暂无数据</div>
                  ) : (
                    <ResponsiveContainer width="100%" height={sceneData.length * 40 + 20}>
                      <BarChart data={sceneData} layout="vertical" margin={{ left: 8, right: 24, top: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" horizontal={false} />
                        <XAxis type="number" tick={{ fill: '#475569', fontSize: 10 }} tickLine={false} axisLine={false} tickFormatter={v => `$${(v / 1000).toFixed(1)}k`} />
                        <YAxis type="category" dataKey="name" tick={{ fill: '#94a3b8', fontSize: 11, fontFamily: 'monospace' }} tickLine={false} axisLine={false} width={76} />
                        <Tooltip content={<DarkTooltip />} />
                        <Bar dataKey="avg_price" name="均价 USDT" radius={[0, 4, 4, 0]} maxBarSize={20}>
                          {sceneData.map((_, i) => (
                            <Cell key={i} fill={`hsl(${155 + i * 9}, 60%, ${50 - i * 2}%)`} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  )}
                </div>
              </div>
            )}

            {/* ════ 排行 ════ */}
            {tab === 'top' && (
              <div className="space-y-2">
                {topAssets.length === 0 ? (
                  <div className="text-xs text-slate-600 py-16 text-center">完成几次估值后，高价值资产将在此处显示</div>
                ) : topAssets.map((asset, i) => {
                  const meta = MODALITY_META[asset.modality] || MODALITY_META.text;
                  const Icon = meta.icon;
                  const rankCls =
                    i === 0 ? 'bg-amber-500/20 text-amber-300 border-amber-500/30' :
                    i === 1 ? 'bg-slate-400/10 text-slate-300 border-slate-500/30' :
                    i === 2 ? 'bg-orange-700/20 text-orange-400 border-orange-700/30' :
                               'bg-slate-800 text-slate-500 border-slate-700';
                  return (
                    <div
                      key={asset.id}
                      className="flex items-center gap-3 p-3 bg-slate-900 border border-slate-800 rounded-xl hover:border-slate-700 transition-all"
                    >
                      <div className={`w-7 h-7 rounded-lg flex items-center justify-center text-xs font-black font-mono shrink-0 border ${rankCls}`}>
                        {i + 1}
                      </div>
                      <Icon className="w-4 h-4 shrink-0" style={{ color: meta.hex }} />
                      <div className="flex-1 min-w-0">
                        <div className="text-xs font-mono text-slate-300 truncate">
                          {asset.description_preview || asset.asset_hash}
                        </div>
                        <div className="text-[10px] font-mono text-slate-600 mt-0.5">
                          {SCENE_LABELS[asset.scene] || asset.scene}
                          {' · '}质量 {Math.round((asset.composite_quality || 0) * 100)}%
                          {' · '}#{asset.id}
                        </div>
                      </div>
                      <div className="text-right shrink-0">
                        <div className="text-sm font-bold font-mono text-purple-300">
                          ${Math.round(asset.dynamic_price || 0).toLocaleString()}
                        </div>
                        <div className="text-[10px] font-mono text-slate-600">USDT</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

          </>
        )}
      </div>
    </div>
  );
}
