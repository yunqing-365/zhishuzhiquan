// src/DatasetMarketplace.jsx — 企业买家数据集市场
/**
 * 知数知圈 · 企业买家市场
 *
 * 与 DatasetCatalog（创作者管理视图）完全分离：
 *   - 无需登录即可浏览（公开端点 /api/market/packages）
 *   - 多维筛选：关键词 / 领域 / 类型 / 价格 / 质量层级
 *   - 排序：质量分 / 价格 / 样本量 / 最新
 *   - 详情抽屉：样本预览（3条脱敏）+ 贡献者数 + Data Card
 *   - 购买流程：需要登录 → 调 /api/dataset/sell → 下载按钮
 *   - 首页统计横幅：总包数 / 总样本数 / 领域分布
 */
import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  X, Search, Database, ShoppingCart, Star, Download,
  Layers, FileText, BarChart2, Users, RefreshCw, Package,
  CheckCircle2, AlertCircle, Loader2, Filter, TrendingUp,
  Award, Zap, ChevronDown, ChevronRight, ArrowUpDown,
  Eye, Lock, Globe, Sparkles, BookOpen, Code, Heart,
  Stethoscope, Scale, GraduationCap, DollarSign, Tag,
} from 'lucide-react';
import { marketClient } from './api';

// ── 常量 ──────────────────────────────────────────────────────────
const DOMAIN_META = {
  medical:   { label: '医疗',   icon: Stethoscope, color: 'text-rose-400',   bg: 'bg-rose-950/30 border-rose-800/40' },
  legal:     { label: '法律',   icon: Scale,        color: 'text-amber-400',  bg: 'bg-amber-950/30 border-amber-800/40' },
  code_tech: { label: '代码',   icon: Code,         color: 'text-cyan-400',   bg: 'bg-cyan-950/30 border-cyan-800/40' },
  education: { label: '教育',   icon: GraduationCap,color: 'text-emerald-400',bg: 'bg-emerald-950/30 border-emerald-800/40' },
  finance:   { label: '金融',   icon: DollarSign,   color: 'text-yellow-400', bg: 'bg-yellow-950/30 border-yellow-800/40' },
  general:   { label: '通用',   icon: Globe,        color: 'text-slate-400',  bg: 'bg-slate-800/40 border-slate-700/40' },
  creative:  { label: '创意',   icon: Sparkles,     color: 'text-purple-400', bg: 'bg-purple-950/30 border-purple-800/40' },
};
const TYPE_META = {
  sft:      { label: 'SFT',  color: 'text-blue-300',   bg: 'bg-blue-900/30 border-blue-800/40' },
  dpo:      { label: 'DPO',  color: 'text-violet-300',  bg: 'bg-violet-900/30 border-violet-800/40' },
  pretrain: { label: '预训练', color: 'text-orange-300', bg: 'bg-orange-900/30 border-orange-800/40' },
};
const QUALITY_TIERS = [
  { key: 'platinum', label: '铂金',  min: 8.5, color: 'text-amber-300',  border: 'border-amber-500/40',  bg: 'bg-amber-900/20' },
  { key: 'gold',     label: '黄金',  min: 7.0, color: 'text-yellow-300', border: 'border-yellow-500/40', bg: 'bg-yellow-900/20' },
  { key: 'silver',   label: '白银',  min: 5.0, color: 'text-slate-300',  border: 'border-slate-500/40',  bg: 'bg-slate-800/40' },
];
const SORT_OPTIONS = [
  { value: 'quality', label: '质量优先' },
  { value: 'samples', label: '样本量' },
  { value: 'price',   label: '价格' },
  { value: 'created', label: '最新上架' },
];

// ── 工具 ──────────────────────────────────────────────────────────
const fmtPrice = (n) =>
  n > 0 ? `¥${Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}` : '免费';
const fmtNum   = (n) => n >= 1e4 ? `${(n / 1e4).toFixed(1)}w` : (n ?? '—').toLocaleString?.() ?? n;
const fmtTime  = (s) => {
  if (!s) return '—';
  try { return new Date(s).toLocaleDateString('zh-CN', { year: '2-digit', month: '2-digit', day: '2-digit' }); }
  catch { return s; }
};
const qualityTier = (score) =>
  score >= 8.5 ? QUALITY_TIERS[0] : score >= 7.0 ? QUALITY_TIERS[1] : QUALITY_TIERS[2];

// ════════════════════════════════════════════════════════════════════
// 子组件
// ════════════════════════════════════════════════════════════════════

// ── 平台统计横幅 ──────────────────────────────────────────────────
function StatsBanner({ stats, loading }) {
  if (loading && !stats) {
    return (
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-16 rounded-xl bg-slate-800/50 animate-pulse" />
        ))}
      </div>
    );
  }
  if (!stats) return null;

  const topDomain = Object.entries(stats.domain_dist || {}).sort((a, b) => b[1] - a[1])[0];
  const domainMeta = topDomain ? (DOMAIN_META[topDomain[0]] || DOMAIN_META.general) : null;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
      {[
        { label: '数据集包',    value: stats.total_packages?.toLocaleString() ?? '—', icon: Package,   color: 'text-cyan-400' },
        { label: '总样本量',    value: fmtNum(stats.total_samples),                    icon: Layers,    color: 'text-emerald-400' },
        { label: '热门领域',    value: domainMeta?.label ?? '—',                       icon: TrendingUp,color: domainMeta?.color ?? 'text-slate-300' },
        { label: '铂金质量包',  value: stats.quality_tiers?.['铂金(≥8.5)'] ?? 0,      icon: Award,     color: 'text-amber-400' },
      ].map(({ label, value, icon: Icon, color }) => (
        <div key={label}
          className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3"
        >
          <Icon size={20} className={`${color} shrink-0`} />
          <div>
            <div className={`text-lg font-bold font-mono leading-none ${color}`}>{value}</div>
            <div className="text-[11px] text-slate-500 mt-0.5">{label}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── 数据集卡片 ────────────────────────────────────────────────────
function MarketCard({ pkg, onSelect, onBuy, buying, purchased }) {
  const {
    package_id, name, dataset_type, domain, total_samples,
    avg_quality, price_cny, created_at, contributor_count,
  } = pkg;

  const tier   = qualityTier(avg_quality ?? 0);
  const dmeta  = DOMAIN_META[domain] || DOMAIN_META.general;
  const tmeta  = TYPE_META[dataset_type];
  const DIcon  = dmeta.icon;

  return (
    <div
      className={`group relative rounded-xl border bg-slate-900/60 hover:bg-slate-900 
                  transition-all duration-200 cursor-pointer flex flex-col overflow-hidden
                  ${purchased
                    ? 'border-emerald-700/60 hover:border-emerald-600'
                    : 'border-slate-800 hover:border-slate-600'}`}
      onClick={() => onSelect(pkg)}
    >
      {/* 购买标记 */}
      {purchased && (
        <div className="absolute top-2.5 right-2.5 flex items-center gap-1 text-[10px] font-semibold
                        text-emerald-400 bg-emerald-950/60 border border-emerald-800/50 px-2 py-0.5 rounded-full z-10">
          <CheckCircle2 size={10} /> 已购
        </div>
      )}

      {/* 领域色条 */}
      <div className={`h-0.5 w-full ${dmeta.color.replace('text-', 'bg-').replace('-400', '-500')}`} />

      <div className="p-4 flex flex-col gap-3 flex-1">
        {/* 头部 */}
        <div className="flex items-start gap-2">
          <div className={`p-1.5 rounded-lg border ${dmeta.bg} shrink-0 mt-0.5`}>
            <DIcon size={13} className={dmeta.color} />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-slate-100 font-semibold text-sm leading-snug line-clamp-2
                           group-hover:text-white transition-colors">
              {name || '未命名数据集'}
            </h3>
            <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
              <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${dmeta.bg} ${dmeta.color}`}>
                {dmeta.label}
              </span>
              {tmeta && (
                <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${tmeta.bg} ${tmeta.color}`}>
                  {tmeta.label}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* 质量分 */}
        <div className={`flex items-center justify-between rounded-lg px-3 py-2 border ${tier.bg} ${tier.border}`}>
          <span className="text-xs text-slate-400">质量分</span>
          <div className="flex items-center gap-2">
            <div className="h-1 w-20 rounded-full bg-slate-800 overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  avg_quality >= 8.5 ? 'bg-amber-400' : avg_quality >= 7 ? 'bg-yellow-400' : 'bg-slate-500'
                }`}
                style={{ width: `${Math.min(100, (avg_quality ?? 0) * 10)}%` }}
              />
            </div>
            <span className={`text-sm font-bold font-mono ${tier.color}`}>
              {avg_quality?.toFixed(1) ?? '—'}
            </span>
            <span className={`text-[10px] font-semibold ${tier.color}`}>{tier.label}</span>
          </div>
        </div>

        {/* 统计 */}
        <div className="grid grid-cols-2 gap-x-3 gap-y-1">
          <div className="flex items-center gap-1.5 text-xs">
            <Layers size={11} className="text-slate-500" />
            <span className="text-slate-500">样本</span>
            <span className="font-mono text-cyan-300 font-semibold">{fmtNum(total_samples)}</span>
          </div>
          <div className="flex items-center gap-1.5 text-xs">
            <Users size={11} className="text-slate-500" />
            <span className="text-slate-500">创作者</span>
            <span className="font-mono text-slate-300">{contributor_count ?? '—'}</span>
          </div>
          <div className="flex items-center gap-1.5 text-xs">
            <Package size={11} className="text-slate-500" />
            <span className="text-slate-500">上架</span>
            <span className="font-mono text-slate-400">{fmtTime(created_at)}</span>
          </div>
        </div>

        {/* 价格 + CTA */}
        <div className="flex items-center justify-between pt-2 border-t border-slate-800 mt-auto">
          <div>
            <div className="text-lg font-bold font-mono text-white">{fmtPrice(price_cny)}</div>
            {price_cny > 0 && (
              <div className="text-[10px] text-slate-600">商业授权</div>
            )}
          </div>
          <button
            onClick={e => { e.stopPropagation(); onBuy(pkg); }}
            disabled={buying === package_id}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
                        transition-all duration-150 ${
              buying === package_id
                ? 'bg-slate-700 text-slate-400 cursor-not-allowed'
                : purchased
                  ? 'bg-emerald-800 hover:bg-emerald-700 text-emerald-200'
                  : price_cny > 0
                    ? 'bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white shadow-lg shadow-cyan-900/30'
                    : 'bg-emerald-700 hover:bg-emerald-600 text-white'
            }`}
          >
            {buying === package_id ? (
              <Loader2 size={11} className="animate-spin" />
            ) : purchased ? (
              <><Download size={11} /> 下载</>
            ) : price_cny > 0 ? (
              <><ShoppingCart size={11} /> 购买</>
            ) : (
              <><Zap size={11} /> 免费获取</>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── 样本预览卡片 ──────────────────────────────────────────────────
function SamplePreview({ sample, index }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-mono text-slate-600">#{index + 1}</span>
        {sample.sample_type && (
          <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border
            ${TYPE_META[sample.sample_type]?.bg ?? 'bg-slate-800 border-slate-700'}
            ${TYPE_META[sample.sample_type]?.color ?? 'text-slate-400'}`}>
            {TYPE_META[sample.sample_type]?.label ?? sample.sample_type}
          </span>
        )}
        {sample.quality_score != null && (
          <span className="text-[10px] font-mono text-amber-400 ml-auto">
            ★ {sample.quality_score.toFixed(1)}
          </span>
        )}
      </div>
      {sample.instruction && (
        <div>
          <div className="text-[10px] text-slate-600 mb-1">指令</div>
          <p className="text-xs text-slate-300 line-clamp-2 leading-relaxed">{sample.instruction}</p>
        </div>
      )}
      {sample.output && (
        <div>
          <div className="text-[10px] text-slate-600 mb-1">输出</div>
          <p className="text-xs text-slate-400 line-clamp-3 leading-relaxed">{sample.output}</p>
        </div>
      )}
      {sample.chosen && (
        <div>
          <div className="text-[10px] text-emerald-700 mb-1">Chosen</div>
          <p className="text-xs text-slate-300 line-clamp-2 leading-relaxed">{sample.chosen}</p>
        </div>
      )}
      {sample.rejected && (
        <div>
          <div className="text-[10px] text-red-900 mb-1">Rejected</div>
          <p className="text-xs text-slate-500 line-clamp-2 leading-relaxed">{sample.rejected}</p>
        </div>
      )}
    </div>
  );
}

// ── 详情抽屉 ─────────────────────────────────────────────────────
function DetailDrawer({ packageId, onClose, onBuy, buying, purchased }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]   = useState('');

  useEffect(() => {
    if (!packageId) return;
    setLoading(true); setError(''); setDetail(null);
    marketClient.getPackage(packageId)
      .then(setDetail)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [packageId]);

  if (!packageId) return null;

  const pkg     = detail;
  const tier    = pkg ? qualityTier(pkg.avg_quality ?? 0) : null;
  const dmeta   = pkg ? (DOMAIN_META[pkg.domain] || DOMAIN_META.general) : null;

  return (
    <div className="fixed inset-0 z-50 flex" onClick={onClose}>
      <div className="flex-1 bg-black/60 backdrop-blur-sm" />
      <div
        className="w-full max-w-lg bg-slate-950 border-l border-slate-800 flex flex-col overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800 shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            {dmeta && <dmeta.icon size={16} className={dmeta.color} />}
            <h2 className="text-white font-semibold text-base truncate">
              {pkg?.name || (loading ? '加载中…' : '数据集详情')}
            </h2>
          </div>
          <button onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition-colors shrink-0">
            <X size={18} />
          </button>
        </div>

        {/* 内容 */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center h-48 text-slate-500 gap-2">
              <Loader2 size={20} className="animate-spin" /><span>加载详情…</span>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center h-48 gap-3 text-slate-500">
              <AlertCircle size={24} className="text-red-500" />
              <p className="text-sm">{error}</p>
            </div>
          ) : pkg ? (
            <div className="p-5 space-y-5">
              {/* 核心指标 */}
              <div className="grid grid-cols-3 gap-3">
                <div className={`rounded-xl border p-3 text-center ${tier.bg} ${tier.border}`}>
                  <div className={`text-2xl font-bold font-mono ${tier.color}`}>
                    {pkg.avg_quality?.toFixed(1) ?? '—'}
                  </div>
                  <div className="text-[10px] text-slate-500 mt-0.5">质量分</div>
                  <div className={`text-[10px] font-semibold mt-1 ${tier.color}`}>{tier.label}</div>
                </div>
                <div className="rounded-xl border border-slate-800 bg-slate-900 p-3 text-center">
                  <div className="text-2xl font-bold font-mono text-cyan-300">
                    {fmtNum(pkg.total_samples)}
                  </div>
                  <div className="text-[10px] text-slate-500 mt-0.5">样本总数</div>
                </div>
                <div className="rounded-xl border border-slate-800 bg-slate-900 p-3 text-center">
                  <div className="text-2xl font-bold font-mono text-white">
                    {fmtPrice(pkg.price_cny)}
                  </div>
                  <div className="text-[10px] text-slate-500 mt-0.5">授权价格</div>
                </div>
              </div>

              {/* 基本信息 */}
              <div className="space-y-2">
                <h3 className="text-slate-500 text-[10px] font-bold uppercase tracking-widest">基本信息</h3>
                {[
                  ['领域',     dmeta?.label ?? pkg.domain ?? '—'],
                  ['数据类型',  TYPE_META[pkg.dataset_type]?.label ?? pkg.dataset_type ?? '—'],
                  ['版本',     pkg.version ?? '1.0.0'],
                  ['创建时间',  fmtTime(pkg.created_at)],
                  ['贡献创作者', `${pkg.contributor_count ?? '—'} 人`],
                  ['SFT样本', pkg.sft_count?.toLocaleString() ?? '—'],
                  ['DPO样本', pkg.dpo_count?.toLocaleString() ?? '—'],
                ].map(([label, value]) => (
                  <div key={label} className="flex items-center justify-between text-sm py-1 border-b border-slate-800/50">
                    <span className="text-slate-500">{label}</span>
                    <span className="text-slate-200">{value}</span>
                  </div>
                ))}
              </div>

              {/* 样本预览 */}
              {pkg.preview_samples?.length > 0 ? (
                <div className="space-y-2">
                  <h3 className="text-slate-500 text-[10px] font-bold uppercase tracking-widest flex items-center gap-1.5">
                    <Eye size={11} /> 样本预览（脱敏）
                  </h3>
                  {pkg.preview_samples.map((s, i) => (
                    <SamplePreview key={i} sample={s} index={i} />
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-700 p-4 text-center text-slate-600 text-xs">
                  <Lock size={14} className="mx-auto mb-1" />
                  样本预览需要购买后可见
                </div>
              )}

              {/* Data Card */}
              <div className="rounded-xl border border-slate-700/40 bg-slate-900/40 p-4 space-y-2">
                <h3 className="text-slate-400 text-xs font-semibold flex items-center gap-1.5">
                  <FileText size={12} /> Data Card
                </h3>
                <div className="text-[11px] text-slate-500 space-y-1">
                  <p>· 许可证：商业内部使用授权（Enterprise Internal）</p>
                  <p>· 语言：中文（简体）为主</p>
                  <p>· 标注方式：LLM 辅助标注 + 人工复核</p>
                  <p>· 三层安全审核：关键词 → 启发式 → LLM</p>
                  <p>· 质检阈值：均质 ≥ {pkg.avg_quality >= 7 ? '7.0（黄金）' : '5.0（白银）'}</p>
                  <p>· 格式：JSONL / Parquet（Snappy 压缩）</p>
                  <p>· ZK 承诺：Poseidon 哈希确权</p>
                </div>
              </div>
            </div>
          ) : null}
        </div>

        {/* 购买区 */}
        {pkg && (
          <div className="p-5 border-t border-slate-800 shrink-0">
            {purchased ? (
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-emerald-400 text-sm font-semibold mb-2">
                  <CheckCircle2 size={15} /> 已购买，可下载
                </div>
                <button
                  onClick={() => onBuy(pkg, true)}
                  className="w-full py-3 rounded-xl font-semibold text-sm transition-all
                             bg-emerald-800 hover:bg-emerald-700 text-white flex items-center justify-center gap-2"
                >
                  <Download size={15} /> 下载数据集
                </button>
              </div>
            ) : (
              <button
                onClick={() => onBuy(pkg)}
                disabled={buying === pkg.package_id}
                className={`w-full py-3 rounded-xl font-semibold text-sm transition-all
                            flex items-center justify-center gap-2
                  ${buying === pkg.package_id
                    ? 'bg-slate-700 text-slate-400 cursor-not-allowed'
                    : pkg.price_cny > 0
                      ? 'bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white shadow-lg shadow-cyan-900/30'
                      : 'bg-emerald-600 hover:bg-emerald-500 text-white'
                  }`}
              >
                {buying === pkg.package_id ? (
                  <><Loader2 size={15} className="animate-spin" /> 处理中…</>
                ) : pkg.price_cny > 0 ? (
                  <><ShoppingCart size={15} /> 购买授权 {fmtPrice(pkg.price_cny)}</>
                ) : (
                  <><Zap size={15} /> 免费获取</>
                )}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── 筛选侧边栏 ────────────────────────────────────────────────────
function FilterPanel({ filters, onChange, stats, onClose }) {
  const { domain, dataset_type, min_quality, max_price, sort_by, order } = filters;

  const setFilter = (key, val) => onChange({ ...filters, [key]: val });

  return (
    <div className="w-64 shrink-0 border-r border-slate-800 bg-slate-950/80 flex flex-col overflow-y-auto">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800">
        <span className="text-slate-300 text-sm font-semibold flex items-center gap-1.5">
          <Filter size={13} /> 筛选
        </span>
        <button onClick={onClose} className="sm:hidden p-1 rounded text-slate-500 hover:text-white">
          <X size={15} />
        </button>
      </div>

      <div className="p-4 space-y-5 flex-1">
        {/* 排序 */}
        <div className="space-y-2">
          <label className="text-[10px] text-slate-500 font-bold uppercase tracking-widest">排序方式</label>
          <div className="grid grid-cols-2 gap-1.5">
            {SORT_OPTIONS.map(opt => (
              <button key={opt.value}
                onClick={() => {
                  if (sort_by === opt.value) setFilter('order', order === 'desc' ? 'asc' : 'desc');
                  else onChange({ ...filters, sort_by: opt.value, order: 'desc' });
                }}
                className={`px-2 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center justify-between gap-1
                  ${sort_by === opt.value
                    ? 'bg-cyan-900/40 border border-cyan-700/60 text-cyan-300'
                    : 'bg-slate-800/60 border border-slate-700/40 text-slate-400 hover:text-slate-200'}`}
              >
                {opt.label}
                {sort_by === opt.value && (
                  <ArrowUpDown size={10} className="opacity-70" />
                )}
              </button>
            ))}
          </div>
        </div>

        {/* 领域 */}
        <div className="space-y-2">
          <label className="text-[10px] text-slate-500 font-bold uppercase tracking-widest">领域</label>
          <div className="space-y-1">
            <button
              onClick={() => setFilter('domain', '')}
              className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-all
                ${!domain ? 'bg-slate-700 text-white' : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'}`}
            >
              全部领域
              {stats?.domain_dist && (
                <span className="ml-1 text-slate-600">
                  ({Object.values(stats.domain_dist).reduce((a, b) => a + b, 0)})
                </span>
              )}
            </button>
            {Object.entries(DOMAIN_META).map(([key, meta]) => {
              const count = stats?.domain_dist?.[key] ?? 0;
              if (!count) return null;
              const DI = meta.icon;
              return (
                <button key={key}
                  onClick={() => setFilter('domain', domain === key ? '' : key)}
                  className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-all flex items-center gap-2
                    ${domain === key
                      ? `${meta.bg} ${meta.color} border`
                      : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'}`}
                >
                  <DI size={11} className={domain === key ? meta.color : 'text-slate-600'} />
                  {meta.label}
                  <span className="ml-auto text-slate-600 font-mono text-[10px]">{count}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* 数据类型 */}
        <div className="space-y-2">
          <label className="text-[10px] text-slate-500 font-bold uppercase tracking-widest">数据类型</label>
          <div className="space-y-1">
            <button
              onClick={() => setFilter('dataset_type', '')}
              className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-all
                ${!dataset_type ? 'bg-slate-700 text-white' : 'text-slate-400 hover:bg-slate-800'}`}
            >全部类型</button>
            {Object.entries(TYPE_META).map(([key, meta]) => (
              <button key={key}
                onClick={() => setFilter('dataset_type', dataset_type === key ? '' : key)}
                className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-all border
                  ${dataset_type === key
                    ? `${meta.bg} ${meta.color}`
                    : 'text-slate-400 border-transparent hover:bg-slate-800 hover:text-slate-200'}`}
              >
                {meta.label}
              </button>
            ))}
          </div>
        </div>

        {/* 质量层级 */}
        <div className="space-y-2">
          <label className="text-[10px] text-slate-500 font-bold uppercase tracking-widest">最低质量</label>
          <div className="space-y-1">
            <button
              onClick={() => setFilter('min_quality', null)}
              className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-all
                ${min_quality == null ? 'bg-slate-700 text-white' : 'text-slate-400 hover:bg-slate-800'}`}
            >全部</button>
            {QUALITY_TIERS.map(t => (
              <button key={t.key}
                onClick={() => setFilter('min_quality', min_quality === t.min ? null : t.min)}
                className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-all border
                  ${min_quality === t.min
                    ? `${t.bg} ${t.color} ${t.border}`
                    : 'text-slate-400 border-transparent hover:bg-slate-800 hover:text-slate-200'}`}
              >
                {t.label}（≥ {t.min}）
              </button>
            ))}
          </div>
        </div>

        {/* 价格上限 */}
        <div className="space-y-2">
          <label className="text-[10px] text-slate-500 font-bold uppercase tracking-widest">价格上限</label>
          <div className="space-y-1">
            {[null, 500, 2000, 5000, 10000].map((v) => (
              <button key={String(v)}
                onClick={() => setFilter('max_price', max_price === v ? null : v)}
                className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-all
                  ${max_price === v
                    ? 'bg-slate-700 text-white'
                    : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'}`}
              >
                {v == null ? '不限' : v === 0 ? '仅免费' : `≤ ¥${v.toLocaleString()}`}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// 主组件
// ════════════════════════════════════════════════════════════════════
export default function DatasetMarketplace({ isOpen, onClose }) {
  // 筛选状态
  const [filters, setFilters] = useState({
    q: '', domain: '', dataset_type: '',
    min_quality: null, max_price: null,
    sort_by: 'quality', order: 'desc',
  });
  const [packages, setPackages] = useState([]);
  const [total, setTotal]       = useState(0);
  const [offset, setOffset]     = useState(0);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState('');

  const [stats, setStats]           = useState(null);
  const [statsLoading, setStatsLoading] = useState(false);

  const [selectedId, setSelectedId] = useState(null);
  const [buying, setBuying]         = useState(null);
  const [buyResult, setBuyResult]   = useState(null);
  const [purchased, setPurchased]   = useState(new Set()); // package_ids

  const [showFilter, setShowFilter] = useState(true); // desktop: visible by default
  const searchRef = useRef(null);
  const PAGE_SIZE = 24;

  // 加载列表
  const loadPackages = useCallback(async (newOffset = 0, currentFilters = filters) => {
    setLoading(true); setError('');
    try {
      const data = await marketClient.listPackages({
        ...currentFilters,
        limit: PAGE_SIZE,
        offset: newOffset,
      });
      if (newOffset === 0) {
        setPackages(data.packages ?? []);
      } else {
        setPackages(prev => [...prev, ...(data.packages ?? [])]);
      }
      setTotal(data.total ?? 0);
      setOffset(newOffset);
    } catch (e) {
      setError(e.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, [filters]);

  // 加载统计
  const loadStats = useCallback(async () => {
    setStatsLoading(true);
    try {
      const s = await marketClient.getStats();
      setStats(s);
    } catch { /* 统计失败不阻断 */ }
    finally { setStatsLoading(false); }
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    loadPackages(0, filters);
    loadStats();
    setTimeout(() => searchRef.current?.focus(), 200);
  }, [isOpen]); // eslint-disable-line

  // 筛选变化时重新加载
  const handleFilterChange = useCallback((newFilters) => {
    setFilters(newFilters);
    loadPackages(0, newFilters);
  }, [loadPackages]);

  // 搜索防抖
  const searchTimer = useRef(null);
  const handleSearch = (q) => {
    const nf = { ...filters, q };
    setFilters(nf);
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => loadPackages(0, nf), 400);
  };

  // 购买/下载
  const handleBuy = useCallback(async (pkg, isDownload = false) => {
    if (isDownload) {
      // marketClient.download 内部调用 getBuyerId()，与购买时完全一致
      try {
        await marketClient.download(pkg.package_id);
      } catch (e) {
        setBuyResult({ success: false, pkg, error: e.message });
      }
      return;
    }

    setBuying(pkg.package_id);
    setBuyResult(null);
    try {
      await marketClient.purchase(pkg.package_id, pkg.price_cny);
      setPurchased(prev => new Set([...prev, pkg.package_id]));
      setBuyResult({ success: true, pkg });
      setSelectedId(null);
    } catch (e) {
      setBuyResult({ success: false, pkg, error: e.message });
    } finally {
      setBuying(null);
    }
  }, []);

  const hasMore = packages.length < total;

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-40 bg-slate-950 flex flex-col">
      {/* ── 顶栏 ─────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 px-5 py-3.5 border-b border-slate-800 bg-slate-950/95 shrink-0">
        <Database size={18} className="text-cyan-400 shrink-0" />
        <div>
          <h1 className="text-white font-bold text-base leading-none">数据集市场</h1>
          <p className="text-[10px] text-slate-500 mt-0.5">企业级语料数据集交易平台</p>
        </div>
        <div className="flex-1" />

        {/* 搜索框 */}
        <div className="relative hidden sm:block w-64">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
          <input
            ref={searchRef}
            value={filters.q}
            onChange={e => handleSearch(e.target.value)}
            placeholder="搜索数据集…"
            className="w-full bg-slate-900 border border-slate-700 rounded-lg pl-8 pr-3 py-2
                       text-xs text-slate-200 placeholder-slate-600
                       focus:outline-none focus:border-cyan-500/60 focus:ring-1 focus:ring-cyan-500/20"
          />
        </div>

        <button
          onClick={() => loadPackages(0)}
          disabled={loading}
          className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
          title="刷新"
        >
          <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
        </button>
        <button
          onClick={() => setShowFilter(s => !s)}
          className={`p-1.5 rounded-lg transition-colors
            ${showFilter ? 'bg-slate-700 text-white' : 'hover:bg-slate-800 text-slate-400 hover:text-white'}`}
          title="筛选面板"
        >
          <Filter size={15} />
        </button>
        <button onClick={onClose}
          className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition-colors">
          <X size={18} />
        </button>
      </div>

      {/* ── 购买结果提示 ──────────────────────────────────────────── */}
      {buyResult && (
        <div className={`mx-5 mt-3 flex items-center gap-2 rounded-lg p-3 text-sm shrink-0
          ${buyResult.success
            ? 'bg-emerald-950/50 border border-emerald-800/50 text-emerald-300'
            : 'bg-red-950/50 border border-red-800/50 text-red-300'}`}>
          {buyResult.success
            ? <><CheckCircle2 size={15} /> 购买成功！分润已自动结算给参与创作者</>
            : <><AlertCircle size={15} /> {buyResult.error}</>
          }
          <button onClick={() => setBuyResult(null)} className="ml-auto p-0.5 hover:text-white">
            <X size={13} />
          </button>
        </div>
      )}

      {/* ── 主体 ─────────────────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0">
        {/* 筛选侧边栏 */}
        {showFilter && (
          <FilterPanel
            filters={filters}
            onChange={handleFilterChange}
            stats={stats}
            onClose={() => setShowFilter(false)}
          />
        )}

        {/* 内容区 */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          {/* 移동端搜索 + 统计 */}
          <div className="px-5 pt-4 shrink-0">
            <div className="sm:hidden relative mb-4">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
              <input
                value={filters.q}
                onChange={e => handleSearch(e.target.value)}
                placeholder="搜索数据集…"
                className="w-full bg-slate-900 border border-slate-700 rounded-lg pl-8 pr-3 py-2
                           text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-cyan-500/60"
              />
            </div>
            <StatsBanner stats={stats} loading={statsLoading} />
            <div className="flex items-center justify-between mb-3">
              <span className="text-slate-500 text-xs">
                {loading ? '加载中…' : `共 ${total.toLocaleString()} 个数据集`}
                {filters.domain && ` · ${DOMAIN_META[filters.domain]?.label}`}
                {filters.dataset_type && ` · ${TYPE_META[filters.dataset_type]?.label}`}
              </span>
            </div>
          </div>

          {/* 卡片网格 */}
          <div className="flex-1 overflow-y-auto px-5 pb-5">
            {loading && packages.length === 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                {[...Array(8)].map((_, i) => (
                  <div key={i} className="h-52 rounded-xl bg-slate-800/40 animate-pulse" />
                ))}
              </div>
            ) : error ? (
              <div className="flex flex-col items-center justify-center h-48 gap-3 text-slate-500">
                <AlertCircle size={24} className="text-red-500" />
                <p className="text-sm">{error}</p>
                <button onClick={() => loadPackages(0)}
                  className="px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-sm text-slate-300 transition-colors">
                  重试
                </button>
              </div>
            ) : packages.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-48 gap-2 text-slate-600">
                <Database size={32} />
                <p className="text-sm">暂无数据集</p>
                <p className="text-xs">尝试调整筛选条件，或等待创作者生产新数据集</p>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                  {packages.map(pkg => (
                    <MarketCard
                      key={pkg.package_id}
                      pkg={pkg}
                      onSelect={p => setSelectedId(p.package_id)}
                      onBuy={handleBuy}
                      buying={buying}
                      purchased={purchased.has(pkg.package_id)}
                    />
                  ))}
                </div>

                {/* 加载更多 */}
                {hasMore && (
                  <div className="flex justify-center mt-5">
                    <button
                      onClick={() => loadPackages(packages.length)}
                      disabled={loading}
                      className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700
                                 border border-slate-700 text-slate-300 text-sm transition-all disabled:opacity-50"
                    >
                      {loading ? (
                        <><Loader2 size={14} className="animate-spin" /> 加载中…</>
                      ) : (
                        <><ChevronDown size={14} /> 加载更多（还有 {total - packages.length} 个）</>
                      )}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      {/* ── 详情抽屉 ─────────────────────────────────────────────── */}
      <DetailDrawer
        packageId={selectedId}
        onClose={() => setSelectedId(null)}
        onBuy={handleBuy}
        buying={buying}
        purchased={purchased.has(selectedId)}
      />
    </div>
  );
}
