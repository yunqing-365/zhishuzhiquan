// src/DatasetCatalog.jsx — 数据集市场目录
/**
 * 知数知圈 · 数据集市场
 * 展示平台上所有已生产的数据集包，支持搜索/筛选/预览/购买
 * 路由：通过 App.jsx 的顶栏"市场"按钮打开（抽屉模式）
 */
import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  X, Search, Database, ShoppingCart, Star, ChevronRight,
  Layers, FileText, BarChart2, Users, RefreshCw, Package,
  CheckCircle2, AlertCircle, Loader2, Filter, TrendingUp,
  Award, Zap, ExternalLink,
} from 'lucide-react';
import { datasetClient } from './api';

// ── 常量 ──────────────────────────────────────────────────────────
const TYPE_LABELS = { sft: 'SFT', dpo: 'DPO', pretrain: '预训练', '': '通用' };
const DOMAIN_LABELS = {
  medical: '医疗', legal: '法律', code_tech: '代码',
  education: '教育', finance: '金融', general: '通用', '': '全部',
};
const QUALITY_COLOR = (score) => {
  if (score >= 8.5) return 'text-amber-400 border-amber-500/40 bg-amber-900/20';
  if (score >= 7.0) return 'text-cyan-400 border-cyan-500/40 bg-cyan-900/20';
  if (score >= 5.0) return 'text-slate-300 border-slate-600 bg-slate-800/40';
  return 'text-red-400 border-red-500/40 bg-red-900/20';
};
const QUALITY_LABEL = (score) => {
  if (score >= 8.5) return '铂金';
  if (score >= 7.0) return '黄金';
  if (score >= 5.0) return '白银';
  return '待审';
};

// ── 工具函数 ──────────────────────────────────────────────────────
const fmtPrice = (n) => n > 0 ? `¥${n.toLocaleString('zh-CN', { minimumFractionDigits: 0 })}` : '免费';
const fmtTime  = (s) => {
  if (!s) return '—';
  try {
    return new Date(s).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', year: '2-digit' });
  } catch { return s; }
};

// ════════════════════════════════════════════════════════════════════
// 子组件
// ════════════════════════════════════════════════════════════════════

function StatPill({ icon: Icon, label, value, color = 'text-slate-300' }) {
  return (
    <div className="flex items-center gap-1.5 text-xs">
      <Icon size={12} className="text-slate-500 shrink-0" />
      <span className="text-slate-500">{label}</span>
      <span className={`font-semibold font-mono ${color}`}>{value}</span>
    </div>
  );
}

function QualityBadge({ score }) {
  return (
    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border font-mono ${QUALITY_COLOR(score)}`}>
      {QUALITY_LABEL(score)} {score?.toFixed?.(1)}
    </span>
  );
}

// ── 数据集卡片 ────────────────────────────────────────────────────
function DatasetCard({ pkg, onSelect, onBuy, buying }) {
  const { package_id, name, dataset_type, domain, total_samples, avg_quality, price_cny, created_at } = pkg;

  return (
    <div
      className="group rounded-xl border border-slate-800 bg-slate-900/60 hover:border-slate-600
                 hover:bg-slate-900 transition-all duration-200 p-4 flex flex-col gap-3 cursor-pointer"
      onClick={() => onSelect(pkg)}
    >
      {/* 头部 */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="text-slate-100 font-semibold text-sm leading-tight truncate group-hover:text-white transition-colors">
            {name || '未命名数据集'}
          </h3>
          <div className="flex items-center gap-1.5 mt-1 flex-wrap">
            {dataset_type && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">
                {TYPE_LABELS[dataset_type] ?? dataset_type}
              </span>
            )}
            {domain && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">
                {DOMAIN_LABELS[domain] ?? domain}
              </span>
            )}
          </div>
        </div>
        <QualityBadge score={avg_quality} />
      </div>

      {/* 统计 */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
        <StatPill icon={Layers}  label="样本数" value={total_samples?.toLocaleString() ?? '—'} color="text-cyan-300" />
        <StatPill icon={BarChart2} label="均质" value={avg_quality?.toFixed(2) ?? '—'} color="text-amber-300" />
        <StatPill icon={Package} label="创建" value={fmtTime(created_at)} />
        <StatPill icon={TrendingUp} label="ID" value={package_id?.slice(0, 8) + '…'} />
      </div>

      {/* 底部：价格 + 购买 */}
      <div className="flex items-center justify-between pt-1 border-t border-slate-800">
        <div className="text-base font-bold font-mono text-white">
          {fmtPrice(price_cny)}
        </div>
        <button
          onClick={(e) => { e.stopPropagation(); onBuy(pkg); }}
          disabled={buying === package_id}
          className={`
            flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all
            ${buying === package_id
              ? 'bg-slate-700 text-slate-400 cursor-not-allowed'
              : price_cny > 0
                ? 'bg-cyan-600 hover:bg-cyan-500 text-white'
                : 'bg-emerald-700 hover:bg-emerald-600 text-white'
            }
          `}
        >
          {buying === package_id ? (
            <Loader2 size={12} className="animate-spin" />
          ) : price_cny > 0 ? (
            <><ShoppingCart size={12} /> 购买</>
          ) : (
            <><Zap size={12} /> 获取</>
          )}
        </button>
      </div>
    </div>
  );
}

// ── 详情抽屉 ─────────────────────────────────────────────────────
function DetailDrawer({ pkg, onClose, onBuy, buying }) {
  if (!pkg) return null;
  const { name, dataset_type, domain, total_samples, avg_quality,
          price_cny, created_at, package_id, creator_contributions } = pkg;

  const contributors = Object.entries(creator_contributions || {});

  return (
    <div className="fixed inset-0 z-50 flex" onClick={onClose}>
      <div className="flex-1 bg-black/50 backdrop-blur-sm" />
      <div
        className="w-full max-w-md bg-slate-950 border-l border-slate-800 flex flex-col overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
          <h2 className="text-white font-semibold text-base truncate pr-4">{name || '数据集详情'}</h2>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* 内容 */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">

          {/* 质量与价格 */}
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl bg-slate-900 border border-slate-800 p-4 text-center">
              <div className="text-2xl font-bold font-mono text-white">{avg_quality?.toFixed(2) ?? '—'}</div>
              <div className="text-xs text-slate-500 mt-1">平均质量分</div>
              <QualityBadge score={avg_quality} />
            </div>
            <div className="rounded-xl bg-slate-900 border border-slate-800 p-4 text-center">
              <div className="text-2xl font-bold font-mono text-white">{fmtPrice(price_cny)}</div>
              <div className="text-xs text-slate-500 mt-1">授权价格</div>
            </div>
          </div>

          {/* 基本信息 */}
          <div className="space-y-2">
            <h3 className="text-slate-400 text-xs font-semibold uppercase tracking-widest">基本信息</h3>
            <InfoRow label="样本总数" value={total_samples?.toLocaleString() ?? '—'} />
            <InfoRow label="数据类型" value={TYPE_LABELS[dataset_type] ?? dataset_type ?? '—'} />
            <InfoRow label="领域" value={DOMAIN_LABELS[domain] ?? domain ?? '—'} />
            <InfoRow label="创建时间" value={fmtTime(created_at)} />
            <InfoRow label="Package ID" value={<span className="font-mono text-[11px] break-all">{package_id}</span>} />
          </div>

          {/* 创作者贡献 */}
          {contributors.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-slate-400 text-xs font-semibold uppercase tracking-widest flex items-center gap-2">
                <Users size={12} /> 创作者贡献
              </h3>
              <div className="space-y-2">
                {contributors.slice(0, 10).map(([cid, ratio]) => (
                  <div key={cid} className="flex items-center gap-2">
                    <div className="w-6 h-6 rounded-full bg-slate-800 flex items-center justify-center text-[10px] text-slate-400 font-mono shrink-0">
                      {cid.slice(0, 2).toUpperCase()}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-xs text-slate-300 font-mono truncate">{cid.slice(0, 12)}…</div>
                      <div className="h-1 rounded-full bg-slate-800 mt-1">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-blue-500"
                          style={{ width: `${Math.min(100, (ratio * 100))}%` }}
                        />
                      </div>
                    </div>
                    <span className="text-xs font-mono text-slate-400 shrink-0">{(ratio * 100).toFixed(1)}%</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Data Card 规范 */}
          <div className="rounded-xl border border-slate-700/50 bg-slate-900/40 p-3 space-y-1.5">
            <div className="text-xs text-slate-400 font-semibold flex items-center gap-1.5">
              <FileText size={12} /> Data Card
            </div>
            <div className="text-[11px] text-slate-500 space-y-0.5">
              <p>· 许可证：商业内部使用授权</p>
              <p>· 语言：中文（简体）为主</p>
              <p>· 标注方式：LLM 辅助 + 人工复核</p>
              <p>· 质检阈值：均质 ≥ {avg_quality >= 7 ? '7.0（黄金）' : '5.0（白银）'}</p>
              <p>· 格式：JSONL / Parquet</p>
            </div>
          </div>
        </div>

        {/* 购买按钮 */}
        <div className="p-5 border-t border-slate-800">
          <button
            onClick={() => onBuy(pkg)}
            disabled={buying === package_id}
            className={`
              w-full py-3 rounded-xl font-semibold text-sm transition-all flex items-center justify-center gap-2
              ${buying === package_id
                ? 'bg-slate-700 text-slate-400 cursor-not-allowed'
                : price_cny > 0
                  ? 'bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white'
                  : 'bg-emerald-600 hover:bg-emerald-500 text-white'
              }
            `}
          >
            {buying === package_id ? (
              <><Loader2 size={15} className="animate-spin" /> 处理中…</>
            ) : price_cny > 0 ? (
              <><ShoppingCart size={15} /> 购买授权 {fmtPrice(price_cny)}</>
            ) : (
              <><Zap size={15} /> 免费获取</>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

function InfoRow({ label, value }) {
  return (
    <div className="flex items-start justify-between gap-3 text-sm">
      <span className="text-slate-500 shrink-0">{label}</span>
      <span className="text-slate-200 text-right">{value}</span>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// 主组件
// ════════════════════════════════════════════════════════════════════

export default function DatasetCatalog({ isOpen, onClose }) {
  const [packages, setPackages]     = useState([]);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState('');
  const [search, setSearch]         = useState('');
  const [filterDomain, setFilterDomain] = useState('');
  const [filterType, setFilterType]     = useState('');
  const [selected, setSelected]     = useState(null);
  const [buying, setBuying]         = useState(null);   // package_id being purchased
  const [buyResult, setBuyResult]   = useState(null);
  const searchRef = useRef(null);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const data = await datasetClient.listPackages(100);
      setPackages(data.packages ?? []);
    } catch (e) {
      setError(e.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) { load(); setTimeout(() => searchRef.current?.focus(), 200); }
  }, [isOpen, load]);

  // 过滤
  const filtered = packages.filter(p => {
    const q = search.toLowerCase();
    const matchSearch = !q ||
      (p.name?.toLowerCase().includes(q)) ||
      (p.domain?.toLowerCase().includes(q)) ||
      (p.dataset_type?.toLowerCase().includes(q));
    const matchDomain = !filterDomain || p.domain === filterDomain;
    const matchType   = !filterType   || p.dataset_type === filterType;
    return matchSearch && matchDomain && matchType;
  });

  const handleBuy = useCallback(async (pkg) => {
    setBuying(pkg.package_id);
    setBuyResult(null);
    try {
      const result = await datasetClient.purchase(pkg.package_id, pkg.price_cny);
      setBuyResult({ success: true, pkg, result });
      setSelected(null);
    } catch (e) {
      setBuyResult({ success: false, pkg, error: e.message });
    } finally {
      setBuying(null);
    }
  }, []);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-40 bg-slate-950 flex flex-col">

      {/* 顶栏 */}
      <div className="flex items-center gap-3 px-5 py-3.5 border-b border-slate-800 bg-slate-950/95">
        <Database size={18} className="text-cyan-400 shrink-0" />
        <h1 className="text-white font-bold text-base">数据集市场</h1>
        <div className="flex-1" />
        <button
          onClick={load}
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

      {/* 搜索 + 筛选 */}
      <div className="flex items-center gap-2 px-5 py-3 border-b border-slate-800/60 bg-slate-950/80 flex-wrap">
        <div className="relative flex-1 min-w-48">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
          <input
            ref={searchRef}
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="搜索数据集名称、领域…"
            className="w-full bg-slate-900 border border-slate-700 rounded-lg pl-9 pr-3 py-2
                       text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-cyan-500/60
                       focus:ring-1 focus:ring-cyan-500/20 transition-all"
          />
        </div>
        <select
          value={filterDomain}
          onChange={e => setFilterDomain(e.target.value)}
          className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300
                     focus:outline-none focus:border-cyan-500/60 transition-all"
        >
          {Object.entries(DOMAIN_LABELS).map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
        <select
          value={filterType}
          onChange={e => setFilterType(e.target.value)}
          className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300
                     focus:outline-none focus:border-cyan-500/60 transition-all"
        >
          <option value="">所有类型</option>
          {Object.entries(TYPE_LABELS).filter(([v]) => v).map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
        <span className="text-slate-600 text-xs shrink-0">{filtered.length} 个</span>
      </div>

      {/* 购买结果提示 */}
      {buyResult && (
        <div className={`mx-5 mt-3 flex items-center gap-2 rounded-lg p-3 text-sm ${
          buyResult.success
            ? 'bg-emerald-950/50 border border-emerald-800/50 text-emerald-300'
            : 'bg-red-950/50 border border-red-800/50 text-red-300'
        }`}>
          {buyResult.success
            ? <><CheckCircle2 size={16} /> 购买成功！分润已结算给参与创作者</>
            : <><AlertCircle size={16} /> {buyResult.error}</>
          }
          <button onClick={() => setBuyResult(null)} className="ml-auto">
            <X size={14} />
          </button>
        </div>
      )}

      {/* 主体内容 */}
      <div className="flex-1 overflow-y-auto p-5">
        {loading && !packages.length ? (
          <div className="flex items-center justify-center h-48 text-slate-500 gap-2">
            <Loader2 size={20} className="animate-spin" />
            <span>加载中…</span>
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center h-48 gap-3 text-slate-500">
            <AlertCircle size={24} className="text-red-500" />
            <p className="text-sm">{error}</p>
            <button onClick={load} className="px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-sm text-slate-300 transition-colors">
              重试
            </button>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 gap-2 text-slate-600">
            <Database size={32} />
            <p className="text-sm">
              {packages.length === 0 ? '暂无数据集，先上传素材并启动生产任务' : '没有匹配的数据集'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
            {filtered.map(pkg => (
              <DatasetCard
                key={pkg.package_id}
                pkg={pkg}
                onSelect={setSelected}
                onBuy={handleBuy}
                buying={buying}
              />
            ))}
          </div>
        )}
      </div>

      {/* 详情抽屉 */}
      <DetailDrawer
        pkg={selected}
        onClose={() => setSelected(null)}
        onBuy={handleBuy}
        buying={buying}
      />
    </div>
  );
}
