// src/CreatorDashboard.jsx — 创作者收益看板
/**
 * 知数知圈 · 创作者控制台
 * 功能：收益汇总 · 素材管理 · 任务监控 · 排行榜
 * 需要已登录（从 tokenStore 读取 creator 信息）
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  X, TrendingUp, Database, Layers, Clock, Award,
  RefreshCw, UploadCloud, Cpu, CheckCircle2, AlertCircle,
  Loader2, ChevronRight, Users, Coins, BarChart2,
  FileText, Package, Star, Bell, ShieldAlert, Download,
  ArrowDownToLine, Activity,
} from 'lucide-react';
import { datasetClient, authClient, tokenStore } from './api';
import BatchUploadPanel from './BatchUploadPanel';

// ── 工具 ──────────────────────────────────────────────────────────
const fmtCny  = (n) => `¥${(n || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtTime = (s) => {
  if (!s) return '—';
  try { return new Date(s).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); }
  catch { return s; }
};

const JOB_STAGE_LABEL = {
  init: '初始化', annotating: '标注中', scoring: '质检中',
  deduplicating: '去重中', packing: '打包中', settling: '结算中',
  done: '完成', failed: '失败',
};
const JOB_STAGE_COLOR = {
  done: 'text-emerald-400', failed: 'text-red-400', annotating: 'text-blue-400',
  scoring: 'text-amber-400', packing: 'text-cyan-400', settling: 'text-purple-400',
};

// ════════════════════════════════════════════════════════════════════
// 子组件
// ════════════════════════════════════════════════════════════════════

function StatCard({ icon: Icon, label, value, sub, accent = 'text-white' }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 flex flex-col gap-2">
      <div className="flex items-center gap-2 text-slate-500 text-xs">
        <Icon size={13} />
        {label}
      </div>
      <div className={`text-2xl font-bold font-mono ${accent} leading-none`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

function MaterialRow({ mat }) {
  const typeColor = {
    text: 'text-blue-400 bg-blue-900/20 border-blue-800/40',
    image: 'text-amber-400 bg-amber-900/20 border-amber-800/40',
    audio: 'text-emerald-400 bg-emerald-900/20 border-emerald-800/40',
    video: 'text-purple-400 bg-purple-900/20 border-purple-800/40',
  }[mat.content_type] ?? 'text-slate-400 bg-slate-800 border-slate-700';

  return (
    <div className="flex items-center gap-3 py-2.5 border-b border-slate-800/60 last:border-0 group">
      <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border shrink-0 ${typeColor}`}>
        {mat.content_type}
      </span>
      <div className="flex-1 min-w-0">
        <p className="text-slate-300 text-xs truncate group-hover:text-slate-100 transition-colors">
          {mat.preview || '（空内容）'}
        </p>
        <p className="text-slate-600 text-[10px] mt-0.5">{mat.metadata?.domain || '—'} · {fmtTime(mat.uploaded_at)}</p>
      </div>
      <span className="text-slate-700 text-[10px] font-mono shrink-0">{mat.material_id?.slice(0, 8)}</span>
    </div>
  );
}

function JobRow({ job }) {
  const stageColor = JOB_STAGE_COLOR[job.stage] ?? 'text-slate-400';
  const isDone     = job.stage === 'done';
  const isFail     = job.stage === 'failed';

  return (
    <div className="flex items-center gap-3 py-2.5 border-b border-slate-800/60 last:border-0">
      <div className={`w-2 h-2 rounded-full shrink-0 ${
        isDone ? 'bg-emerald-500' : isFail ? 'bg-red-500' : 'bg-blue-400 animate-pulse'
      }`} />
      <div className="flex-1 min-w-0">
        <p className="text-slate-300 text-xs truncate">{job.name || job.job_id?.slice(0, 16) + '…'}</p>
        <div className="flex items-center gap-2 mt-0.5">
          <span className={`text-[10px] font-mono ${stageColor}`}>
            {JOB_STAGE_LABEL[job.stage] ?? job.stage}
          </span>
          {job.total_materials > 0 && (
            <span className="text-slate-600 text-[10px]">{job.total_materials} 条素材</span>
          )}
        </div>
      </div>
      <span className="text-slate-600 text-[10px] font-mono shrink-0">{fmtTime(job.started_at)}</span>
    </div>
  );
}

function LeaderRow({ rank, item }) {
  const medals = ['🥇', '🥈', '🥉'];
  return (
    <div className="flex items-center gap-3 py-2.5 border-b border-slate-800/60 last:border-0">
      <span className="text-base w-6 shrink-0 text-center">
        {rank <= 3 ? medals[rank - 1] : <span className="text-slate-600 text-xs font-mono">#{rank}</span>}
      </span>
      <div className="flex-1 min-w-0">
        <p className="text-slate-300 text-xs font-mono truncate">{item.creator_id?.slice(0, 20)}…</p>
        <p className="text-slate-600 text-[10px] mt-0.5">{item.dataset_count ?? 0} 个数据集</p>
      </div>
      <span className="text-amber-400 font-mono text-xs font-semibold shrink-0">
        {fmtCny(item.total_earned)}
      </span>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// 主组件
// ════════════════════════════════════════════════════════════════════

export default function CreatorDashboard({ isOpen, onClose }) {
  const creator = tokenStore.getCreator();

  const [tab, setTab]               = useState('overview');  // overview | materials | jobs | leaderboard
  const [earnings, setEarnings]     = useState(null);
  const [materials, setMaterials]   = useState([]);
  const [jobs, setJobs]             = useState([]);
  const [leaderboard, setLeaderboard] = useState([]);
  const [platformStats, setPlatformStats] = useState(null);
  const [loading, setLoading]       = useState(false);
  const [showBatchUpload, setShowBatchUpload] = useState(false);
  const [error, setError]           = useState('');
  // v3 新增
  const [ledger, setLedger]         = useState([]);
  const [balance, setBalance]       = useState(null);
  const [monitor, setMonitor]       = useState(null);
  const [alerts, setAlerts]         = useState([]);
  const [versions, setVersions]     = useState([]);

  const loadAll = useCallback(async () => {
    if (!isOpen) return;
    setLoading(true); setError('');
    try {
      const [earningsRes, matsRes, jobsRes, lbRes, statsRes, balRes, ledgerRes, monRes, alertsRes, versionsRes] = await Promise.allSettled([
        creator ? datasetClient.myEarnings() : Promise.resolve(null),
        creator ? datasetClient.listMaterials(50) : Promise.resolve({ materials: [] }),
        datasetClient.listJobs(30),
        datasetClient.leaderboard(10),
        datasetClient.platformStats(),
        creator ? datasetClient.myBalance() : Promise.resolve(null),
        creator ? datasetClient.myLedger(30) : Promise.resolve(null),
        datasetClient.monitorSnapshot(),
        datasetClient.alerts(false, 20),
        datasetClient.listVersions(null, 20),
      ]);

      if (earningsRes.status === 'fulfilled' && earningsRes.value)
        setEarnings(earningsRes.value);
      if (matsRes.status === 'fulfilled')
        setMaterials(matsRes.value.materials ?? []);
      if (jobsRes.status === 'fulfilled')
        setJobs(jobsRes.value.jobs ?? []);
      if (lbRes.status === 'fulfilled')
        setLeaderboard(lbRes.value.leaderboard ?? []);
      if (statsRes.status === 'fulfilled')
        setPlatformStats(statsRes.value);
      if (balRes.status === 'fulfilled' && balRes.value)
        setBalance(balRes.value);
      if (ledgerRes.status === 'fulfilled' && ledgerRes.value)
        setLedger(ledgerRes.value.entries ?? []);
      if (monRes.status === 'fulfilled')
        setMonitor(monRes.value);
      if (alertsRes.status === 'fulfilled')
        setAlerts(alertsRes.value.alerts ?? []);
      if (versionsRes.status === 'fulfilled')
        setVersions(versionsRes.value.versions ?? []);
    } catch (e) {
      setError(e.message || '数据加载失败');
    } finally {
      setLoading(false);
    }
  }, [isOpen, creator]);

  useEffect(() => { loadAll(); }, [loadAll]);

  // 批量上传完成后刷新素材列表
  const handleBatchUploaded = useCallback((ids) => {
    setShowBatchUpload(false);
    loadAll();
  }, [loadAll]);

  if (!isOpen) return null;

  const TABS = [
    { id: 'overview',     label: '总览',   icon: BarChart2 },
    { id: 'materials',    label: `素材 ${materials.length}`, icon: Database },
    { id: 'jobs',         label: `任务 ${jobs.length}`,      icon: Cpu },
    { id: 'ledger',       label: '账本',   icon: FileText },
    { id: 'monitor',      label: alerts.length > 0 ? `告警 ${alerts.length}` : '监控', icon: alerts.length > 0 ? Bell : Activity },
    { id: 'leaderboard',  label: '排行榜',  icon: Award },
  ];

  return (
    <div className="fixed inset-0 z-40 bg-slate-950 flex flex-col">

      {/* 顶栏 */}
      <div className="flex items-center gap-3 px-5 py-3.5 border-b border-slate-800 bg-slate-950/95 shrink-0">
        <TrendingUp size={18} className="text-emerald-400 shrink-0" />
        <div className="min-w-0">
          <h1 className="text-white font-bold text-base leading-none">创作者控制台</h1>
          {creator && (
            <p className="text-slate-500 text-[11px] mt-0.5 font-mono">{creator.display_name || creator.username}</p>
          )}
        </div>
        <div className="flex-1" />
        <button
          onClick={loadAll}
          disabled={loading}
          className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
          title="刷新"
        >
          <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
        </button>
        <button
          onClick={onClose}
          className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
        >
          <X size={18} />
        </button>
      </div>

      {/* Tab 栏 */}
      <div className="flex gap-1 px-5 py-2 border-b border-slate-800/60 shrink-0 overflow-x-auto">
        {TABS.map(t => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`
                flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all whitespace-nowrap
                ${tab === t.id
                  ? 'bg-slate-800 text-white border border-slate-700'
                  : 'text-slate-500 hover:text-slate-300 hover:bg-slate-900'
                }
              `}
            >
              <Icon size={12} />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* 主体 */}
      <div className="flex-1 overflow-y-auto p-5">
        {loading && !earnings && !materials.length ? (
          <div className="flex items-center justify-center h-48 text-slate-500 gap-2">
            <Loader2 size={20} className="animate-spin" />
            <span>加载中…</span>
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center h-48 gap-3 text-slate-500">
            <AlertCircle size={24} className="text-red-500" />
            <p className="text-sm">{error}</p>
          </div>
        ) : (
          <>
            {/* ── 总览 Tab ─────────────────────────────────────────── */}
            {tab === 'overview' && (
              <div className="space-y-5">
                {/* 我的收益 */}
                {creator && (earnings || balance) && (
                  <div>
                    <h2 className="text-slate-400 text-xs font-semibold uppercase tracking-widest mb-3 flex items-center gap-2">
                      <Coins size={12} /> 我的收益
                      {balance && <span className="text-[10px] normal-case font-normal text-emerald-500 border border-emerald-800/40 bg-emerald-900/20 px-1.5 py-0.5 rounded font-mono">SQLite ✓</span>}
                    </h2>
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                      <StatCard
                        icon={TrendingUp}
                        label="账本余额"
                        value={fmtCny(balance?.balance_cny ?? earnings?.balance?.total_earned ?? 0)}
                        accent="text-amber-400"
                      />
                      <StatCard
                        icon={CheckCircle2}
                        label="累计收益"
                        value={fmtCny(balance?.revenue_summary?.total_earned ?? earnings?.balance?.paid ?? 0)}
                        accent="text-emerald-400"
                      />
                      <StatCard
                        icon={Clock}
                        label="待结算"
                        value={fmtCny(balance?.revenue_summary?.pending ?? earnings?.balance?.pending ?? 0)}
                        accent="text-cyan-400"
                      />
                    </div>
                  </div>
                )}

                {/* 平台统计 */}
                {platformStats && (
                  <div>
                    <h2 className="text-slate-400 text-xs font-semibold uppercase tracking-widest mb-3 flex items-center gap-2">
                      <BarChart2 size={12} /> 平台统计
                    </h2>
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                      <StatCard icon={Database}  label="总样本量"  value={(platformStats.total_samples ?? platformStats.total_materials ?? 0).toLocaleString()} />
                      <StatCard icon={Package}   label="数据集包"  value={(platformStats.packages ?? platformStats.total_packages ?? 0).toLocaleString()} />
                      <StatCard icon={Users}     label="创作者数"  value={(platformStats.creator_count ?? platformStats.total_creators ?? 0).toLocaleString()} />
                      <StatCard icon={Coins}     label="平台总收益" value={fmtCny(platformStats.total_revenue ?? platformStats.total_revenue_cny ?? 0)} accent="text-amber-400" />
                      <StatCard icon={Cpu}       label="SFT样本"   value={(platformStats.sft_samples ?? 0).toLocaleString()} />
                      <StatCard icon={Star}      label="DPO样本"   value={(platformStats.dpo_samples ?? 0).toLocaleString()} accent="text-cyan-400" />
                    </div>
                  </div>
                )}

                {/* 最近收益记录 */}
                {creator && earnings?.records?.length > 0 && (
                  <div>
                    <h2 className="text-slate-400 text-xs font-semibold uppercase tracking-widest mb-3 flex items-center gap-2">
                      <FileText size={12} /> 最近收益记录
                    </h2>
                    <div className="rounded-xl border border-slate-800 bg-slate-900/40 divide-y divide-slate-800/60">
                      {earnings.records.slice(0, 5).map((r, i) => (
                        <div key={i} className="flex items-center gap-3 px-4 py-3">
                          <div className="flex-1 min-w-0">
                            <p className="text-slate-300 text-xs font-mono truncate">
                              {r.package_id?.slice(0, 20)}…
                            </p>
                            <p className="text-slate-600 text-[10px] mt-0.5">
                              贡献 {(r.contribution_ratio * 100).toFixed(1)}% · {fmtTime(r.created_at)}
                            </p>
                          </div>
                          <span className={`font-mono text-sm font-bold shrink-0 ${
                            r.status === 'paid' ? 'text-emerald-400' : 'text-amber-400'
                          }`}>
                            {fmtCny(r.creator_share)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {!creator && (
                  <div className="rounded-xl border border-slate-700 bg-slate-900/40 p-6 text-center space-y-2">
                    <p className="text-slate-400 text-sm">登录后查看个人收益</p>
                    <p className="text-slate-600 text-xs">使用顶栏的登录/注册功能</p>
                  </div>
                )}
              </div>
            )}

            {/* ── 素材 Tab ─────────────────────────────────────────── */}
            {tab === 'materials' && (
              <div className="space-y-4">
                {/* 批量上传入口 */}
                {creator && (
                  <div>
                    {showBatchUpload ? (
                      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
                        <BatchUploadPanel
                          onUploaded={handleBatchUploaded}
                          onCancel={() => setShowBatchUpload(false)}
                        />
                      </div>
                    ) : (
                      <button
                        onClick={() => setShowBatchUpload(true)}
                        className="w-full flex items-center justify-center gap-2 py-3 rounded-xl border border-dashed border-slate-700
                                   text-slate-400 hover:text-slate-200 hover:border-slate-500 hover:bg-slate-900/40 transition-all text-sm"
                      >
                        <UploadCloud size={16} /> 批量上传素材（CSV / JSONL / ZIP / TXT）
                      </button>
                    )}
                  </div>
                )}

                {/* 素材列表 */}
                {materials.length > 0 ? (
                  <div className="rounded-xl border border-slate-800 bg-slate-900/40 px-4 divide-y divide-slate-800/40">
                    {materials.map(m => <MaterialRow key={m.material_id} mat={m} />)}
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center h-40 text-slate-600 gap-2">
                    <Database size={28} />
                    <p className="text-sm">{creator ? '暂无素材，点击上方上传' : '登录后查看素材'}</p>
                  </div>
                )}
              </div>
            )}

            {/* ── 任务 Tab ─────────────────────────────────────────── */}
            {tab === 'jobs' && (
              <div>
                {jobs.length > 0 ? (
                  <div className="rounded-xl border border-slate-800 bg-slate-900/40 px-4 divide-y divide-slate-800/40">
                    {jobs.map((j, i) => <JobRow key={j.job_id ?? i} job={j} />)}
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center h-40 text-slate-600 gap-2">
                    <Cpu size={28} />
                    <p className="text-sm">暂无生产任务</p>
                  </div>
                )}
              </div>
            )}

            {/* ── 账本 Tab（v3 SQLite 持久化）──────────────────────── */}
            {tab === 'ledger' && (
              <div className="space-y-4">
                {!creator ? (
                  <div className="flex flex-col items-center justify-center h-40 text-slate-600 gap-2">
                    <FileText size={28} />
                    <p className="text-sm">登录后查看账本流水</p>
                  </div>
                ) : (
                  <>
                    {balance && (
                      <div className="grid grid-cols-2 gap-3 mb-2">
                        <StatCard icon={Coins} label="当前余额" value={fmtCny(balance.balance_cny ?? 0)} accent="text-amber-400" />
                        <StatCard icon={CheckCircle2} label="收益笔数" value={balance.revenue_summary?.record_count ?? 0} />
                      </div>
                    )}
                    <h2 className="text-slate-400 text-xs font-semibold uppercase tracking-widest flex items-center gap-2">
                      <FileText size={12} /> 账本流水（SQLite 持久化）
                    </h2>
                    {ledger.length > 0 ? (
                      <div className="rounded-xl border border-slate-800 bg-slate-900/40 divide-y divide-slate-800/60">
                        {ledger.map((entry, i) => (
                          <div key={entry.entry_id ?? i} className="flex items-center gap-3 px-4 py-3">
                            <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border shrink-0 ${
                              entry.entry_type === 'credit'
                                ? 'text-emerald-400 bg-emerald-900/20 border-emerald-800/40'
                                : 'text-red-400 bg-red-900/20 border-red-800/40'
                            }`}>
                              {entry.entry_type === 'credit' ? '入账' : '支出'}
                            </span>
                            <div className="flex-1 min-w-0">
                              <p className="text-slate-300 text-xs truncate">{entry.note || '—'}</p>
                              <p className="text-slate-600 text-[10px] mt-0.5">{fmtTime(entry.created_at)} · 余额 {fmtCny(entry.balance_after)}</p>
                            </div>
                            <span className={`font-mono text-sm font-bold shrink-0 ${
                              entry.entry_type === 'credit' ? 'text-emerald-400' : 'text-red-400'
                            }`}>
                              {entry.entry_type === 'credit' ? '+' : '-'}{fmtCny(entry.amount)}
                            </span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="flex flex-col items-center justify-center h-40 text-slate-600 gap-2">
                        <FileText size={28} />
                        <p className="text-sm">暂无账本记录</p>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {/* ── 监控 Tab（v3 P0 监控埋点）────────────────────────── */}
            {tab === 'monitor' && (
              <div className="space-y-5">
                {/* 未解决告警 */}
                {alerts.length > 0 && (
                  <div>
                    <h2 className="text-slate-400 text-xs font-semibold uppercase tracking-widest mb-3 flex items-center gap-2">
                      <Bell size={12} className="text-red-400" />
                      <span className="text-red-400">未解决告警 {alerts.length}</span>
                    </h2>
                    <div className="rounded-xl border border-red-900/40 bg-red-950/20 divide-y divide-red-900/30">
                      {alerts.map((a, i) => (
                        <div key={a.alert_id ?? i} className="px-4 py-3 flex items-start gap-3">
                          <ShieldAlert size={14} className={
                            a.severity === 'CRITICAL' ? 'text-red-400 shrink-0 mt-0.5' :
                            a.severity === 'ERROR'    ? 'text-orange-400 shrink-0 mt-0.5' :
                                                        'text-amber-400 shrink-0 mt-0.5'
                          } />
                          <div className="flex-1 min-w-0">
                            <p className="text-slate-300 text-xs">{a.message}</p>
                            <p className="text-slate-600 text-[10px] mt-0.5">
                              {a.severity} · {a.stage} · {fmtTime(a.created_at)}
                            </p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* 最近 job 阶段指标 */}
                {monitor?.jobs?.length > 0 && (
                  <div>
                    <h2 className="text-slate-400 text-xs font-semibold uppercase tracking-widest mb-3 flex items-center gap-2">
                      <Activity size={12} /> 最近生产任务指标
                    </h2>
                    <div className="rounded-xl border border-slate-800 bg-slate-900/40 divide-y divide-slate-800/60">
                      {monitor.jobs.map((j, i) => (
                        <div key={j.job_id ?? i} className="px-4 py-3 flex items-center gap-3">
                          <div className="flex-1 min-w-0">
                            <p className="text-slate-300 text-xs font-mono truncate">{j.job_id?.slice(0, 16)}…</p>
                            <p className="text-slate-600 text-[10px] mt-0.5">
                              {j.stages_done} 阶段 · 输入 {j.total_input} → 输出 {j.total_output} · {j.total_time_s}s
                            </p>
                          </div>
                          <div className="flex flex-col items-end gap-1">
                            <span className="text-cyan-400 font-mono text-xs">质量 {j.avg_quality?.toFixed(1) ?? '—'}</span>
                            {j.has_alert && <span className="text-[10px] text-red-400 font-mono">⚠ 有告警</span>}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {!monitor && !alerts.length && (
                  <div className="flex flex-col items-center justify-center h-40 text-slate-600 gap-2">
                    <Activity size={28} />
                    <p className="text-sm">暂无监控数据</p>
                  </div>
                )}

                {/* 版本历史（versioning v2 — SQLite）*/}
                {versions.length > 0 && (
                  <div>
                    <h2 className="text-slate-400 text-xs font-semibold uppercase tracking-widest mb-3 flex items-center gap-2">
                      <ArrowDownToLine size={12} /> 版本快照记录
                    </h2>
                    <div className="rounded-xl border border-slate-800 bg-slate-900/40 divide-y divide-slate-800/60">
                      {versions.slice(0, 8).map((v, i) => (
                        <div key={v.version_id ?? i} className="px-4 py-3 flex items-center gap-3">
                          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-cyan-400 border border-slate-700 shrink-0">
                            v{v.version}
                          </span>
                          <div className="flex-1 min-w-0">
                            <p className="text-slate-300 text-xs font-medium truncate">{v.name}</p>
                            <p className="text-slate-600 text-[10px] mt-0.5">
                              {v.total_samples} 样本 · 质量 {v.avg_quality?.toFixed(2)}
                              {v.delta_samples !== 0 && (
                                <span className={v.delta_samples > 0 ? 'text-emerald-500' : 'text-red-500'}>
                                  {' '}({v.delta_samples > 0 ? '+' : ''}{v.delta_samples})
                                </span>
                              )}
                              {' · '}{fmtTime(v.created_at)}
                            </p>
                          </div>
                          {v.export_paths?.sft_parquet && (
                            <span className="text-[10px] font-mono text-violet-400 shrink-0">Parquet</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* ── 排行榜 Tab ───────────────────────────────────────── */}
            {tab === 'leaderboard' && (
              <div>
                <div className="flex items-center gap-2 mb-4">
                  <Award size={15} className="text-amber-400" />
                  <h2 className="text-slate-300 text-sm font-semibold">创作者收益排行榜</h2>
                </div>
                {leaderboard.length > 0 ? (
                  <div className="rounded-xl border border-slate-800 bg-slate-900/40 px-4 divide-y divide-slate-800/40">
                    {leaderboard.map((item, i) => (
                      <LeaderRow key={item.creator_id ?? i} rank={i + 1} item={item} />
                    ))}
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center h-40 text-slate-600 gap-2">
                    <Award size={28} />
                    <p className="text-sm">排行榜暂无数据</p>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
