/**
 * CollisionPanel.jsx — 相似资产碰撞检测面板
 * ============================================
 * 功能：
 *   - 输入资产描述或直接传入 embedding，调用 POST /api/detect_collision
 *   - 实时展示碰撞判决（COLLISION / WARNING / SAFE）、风险分、Top-K 相似资产列表
 *   - 每条相似资产显示相似度进度条、模态图标、场景标签、资产哈希摘要
 *
 * 使用方式（在 OracleValuationScreen 中调用）：
 *   <CollisionPanel
 *     isOpen={showCollision}
 *     onClose={() => setShowCollision(false)}
 *     prefill={{ description: asset.description, asset_category: asset.assetCategory }}
 *     // 可选：估值完成后直接传入 embedding，跳过向量化步骤
 *     embedding={valuationResult?.meta?.embedding}
 *     excludeHash={valuationResult?.asset_hash}
 *   />
 */
import React, { useState, useCallback } from 'react';
import {
  Shield, ShieldAlert, ShieldCheck, ShieldX,
  Search, Loader2, X, AlertTriangle, CheckCircle2,
  FileText, Image, Mic, Video, ChevronDown, ChevronUp,
} from 'lucide-react';
import { detectCollision } from './api';

// ── 常量 ─────────────────────────────────────────────────────────────
const MODALITY_ICON = { text: FileText, image: Image, audio: Mic, video: Video };
const MODALITY_LABEL = { text: '文本', image: '图像', audio: '音频', video: '视频' };
const SCENE_LABELS = {
  medical_sft: '医疗 SFT', legal_doc: '法律文书', code_tech: '代码技术',
  creative: '创意写作', chat_qa: '问答对话', illustration: '原创插画',
  photo: '摄影作品', diagram: '图表图解', screenshot: '截图素材',
  speech_medical: '医疗语音', speech_legal: '法律音频', speech_edu: '教育语音',
  music_original: '原创音乐', ambient_sfx: '环境音效', general: '通用',
  documentary: '纪录/访谈', lecture: '教学讲解', cinematic: '影视创作',
  sports_action: '运动/动作', vlog: '个人 vlog',
};

// 判决配置
const VERDICT_CFG = {
  COLLISION: {
    icon:       ShieldX,
    label:      '版权碰撞',
    sublabel:   '检测到高度相似资产，建议拒绝上链',
    bg:         'bg-red-500/10',
    border:     'border-red-500/40',
    iconColor:  'text-red-400',
    badge:      'bg-red-500/20 text-red-400 border-red-500/30',
    bar:        '#ef4444',
  },
  WARNING: {
    icon:       ShieldAlert,
    label:      '相似警告',
    sublabel:   '发现相似内容，建议人工审核',
    bg:         'bg-amber-500/10',
    border:     'border-amber-500/40',
    iconColor:  'text-amber-400',
    badge:      'bg-amber-500/20 text-amber-400 border-amber-500/30',
    bar:        '#f59e0b',
  },
  SAFE: {
    icon:       ShieldCheck,
    label:      '内容独特',
    sublabel:   '未检测到相似资产，可安全上链',
    bg:         'bg-emerald-500/10',
    border:     'border-emerald-500/40',
    iconColor:  'text-emerald-400',
    badge:      'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
    bar:        '#10b981',
  },
  EMPTY_CORPUS: {
    icon:       Shield,
    label:      '向量库为空',
    sublabel:   '首个资产，无对比基准，直接上链',
    bg:         'bg-slate-500/10',
    border:     'border-slate-500/40',
    iconColor:  'text-slate-400',
    badge:      'bg-slate-500/20 text-slate-400 border-slate-500/30',
    bar:        '#64748b',
  },
};

const RISK_LEVEL_CFG = {
  COLLISION: { label: '碰撞', color: 'text-red-400',   bg: 'bg-red-500/10   border-red-500/20' },
  WARNING:   { label: '警告', color: 'text-amber-400', bg: 'bg-amber-500/10 border-amber-500/20' },
  SAFE:      { label: '安全', color: 'text-emerald-400', bg: 'bg-emerald-500/10 border-emerald-500/20' },
};

// ── 相似资产卡片 ──────────────────────────────────────────────────────
function MatchCard({ match, rank }) {
  const [expanded, setExpanded] = useState(false);
  const Icon    = MODALITY_ICON[match.modality] || FileText;
  const lvlCfg  = RISK_LEVEL_CFG[match.risk_level] || RISK_LEVEL_CFG.SAFE;
  const simPct  = Math.round(match.similarity_score * 100);
  const barColor = match.risk_level === 'COLLISION' ? '#ef4444'
                 : match.risk_level === 'WARNING'   ? '#f59e0b'
                 : '#10b981';

  return (
    <div className={`rounded-xl border ${lvlCfg.bg} transition-all`}>
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer"
        onClick={() => setExpanded(v => !v)}
      >
        {/* 排名 */}
        <div className="w-5 text-center text-[10px] font-mono text-slate-600 shrink-0">
          #{rank}
        </div>

        {/* 模态图标 */}
        <div className="w-6 h-6 rounded-md bg-slate-800 flex items-center justify-center shrink-0">
          <Icon className="w-3 h-3 text-slate-400" />
        </div>

        {/* 风险等级标签 */}
        <span className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-full border ${lvlCfg.bg} ${lvlCfg.color} shrink-0`}>
          {lvlCfg.label}
        </span>

        {/* 哈希摘要 */}
        <div className="flex-1 min-w-0">
          <div className="text-[11px] font-mono text-slate-400 truncate">
            {match.asset_hash?.slice(0, 20)}…
          </div>
        </div>

        {/* 相似度进度条 */}
        <div className="flex items-center gap-2 shrink-0">
          <div className="w-20 h-1.5 bg-slate-800 rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{ width: `${simPct}%`, backgroundColor: barColor }}
            />
          </div>
          <span className={`text-xs font-mono font-bold ${lvlCfg.color} w-8 text-right`}>
            {simPct}%
          </span>
        </div>

        {/* 展开箭头 */}
        <div className="text-slate-600 shrink-0">
          {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        </div>
      </div>

      {/* 展开详情 */}
      {expanded && (
        <div className="px-4 pb-3 pt-0 border-t border-slate-800/50 grid grid-cols-2 gap-x-6 gap-y-1.5">
          {[
            ['模态',     MODALITY_LABEL[match.modality] || match.modality],
            ['场景',     SCENE_LABELS[match.scene] || match.scene],
            ['音频场景', match.audio_scene ? (SCENE_LABELS[match.audio_scene] || match.audio_scene) : '—'],
            ['余弦距离', match.distance?.toFixed(4)],
            ['相似度',   `${simPct}%`],
            ['资产哈希', match.asset_hash?.slice(0, 28) + '…'],
          ].map(([k, v]) => (
            <div key={k} className="flex gap-2 mt-1.5">
              <span className="text-[10px] font-mono text-slate-600 w-16 shrink-0">{k}</span>
              <span className="text-[10px] font-mono text-slate-400 break-all">{v}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── 主组件 ────────────────────────────────────────────────────────────
export default function CollisionPanel({
  isOpen,
  onClose,
  prefill = {},        // { description, asset_category }
  embedding = null,   // 预计算向量（可选）
  excludeHash = '',   // 排除自身哈希（可选）
}) {
  const [description,    setDescription]    = useState(prefill.description    || '');
  const [assetCategory,  setAssetCategory]  = useState(prefill.asset_category || 'text');
  const [topK,           setTopK]           = useState(8);
  const [loading,        setLoading]        = useState(false);
  const [report,         setReport]         = useState(null);
  const [error,          setError]          = useState(null);

  const handleDetect = useCallback(async () => {
    if (!description.trim() && !embedding) return;
    setLoading(true);
    setReport(null);
    setError(null);
    try {
      const payload = {
        asset_category: assetCategory,
        top_k:          topK,
        exclude_hash:   excludeHash,
        ...(embedding
          ? { embedding }
          : { description: description.trim() }),
      };
      const data = await detectCollision(payload);
      setReport(data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || '检测失败');
    } finally {
      setLoading(false);
    }
  }, [description, assetCategory, embedding, excludeHash, topK]);

  if (!isOpen) return null;

  const vcfg = report ? (VERDICT_CFG[report.verdict] || VERDICT_CFG.SAFE) : null;
  const VIcon = vcfg?.icon;

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-[#0d1117] border border-slate-800 rounded-2xl shadow-2xl w-full max-w-xl max-h-[90vh] flex flex-col">

        {/* 标题栏 */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <Shield className="w-4 h-4 text-purple-400" />
            <span className="text-sm font-mono font-bold text-slate-200">版权碰撞检测</span>
            <span className="text-[10px] font-mono text-slate-600 ml-1">ChromaDB ANN</span>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg text-slate-600 hover:text-slate-300 hover:bg-slate-800 transition-all">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* 内容区（可滚动） */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">

          {/* 输入区 */}
          <div className="space-y-3">
            <div className="flex gap-2">
              {/* 模态选择 */}
              <select
                value={assetCategory}
                onChange={e => setAssetCategory(e.target.value)}
                disabled={!!embedding}
                className="bg-slate-900 border border-slate-700 text-slate-300 text-xs font-mono rounded-lg px-2 py-1.5 focus:outline-none focus:border-purple-500 disabled:opacity-50"
              >
                {['text','image','audio','video'].map(m => (
                  <option key={m} value={m}>{MODALITY_LABEL[m]}</option>
                ))}
              </select>

              {/* Top-K */}
              <select
                value={topK}
                onChange={e => setTopK(Number(e.target.value))}
                className="bg-slate-900 border border-slate-700 text-slate-300 text-xs font-mono rounded-lg px-2 py-1.5 focus:outline-none focus:border-purple-500"
              >
                {[3,5,8,10,15,20].map(k => (
                  <option key={k} value={k}>Top-{k}</option>
                ))}
              </select>

              <div className="flex-1" />

              {/* 检测按钮 */}
              <button
                onClick={handleDetect}
                disabled={loading || (!description.trim() && !embedding)}
                className="flex items-center gap-1.5 px-4 py-1.5 bg-purple-600 hover:bg-purple-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-mono rounded-lg transition-all"
              >
                {loading
                  ? <><Loader2 className="w-3 h-3 animate-spin" />检测中…</>
                  : <><Search className="w-3 h-3" />开始检测</>}
              </button>
            </div>

            {/* 描述输入框（embedding 模式时隐藏） */}
            {!embedding && (
              <textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="输入资产描述内容，系统将在向量库中检索相似资产…"
                rows={3}
                className="w-full bg-slate-900 border border-slate-700 text-slate-300 text-xs font-mono rounded-lg px-3 py-2.5 resize-none focus:outline-none focus:border-purple-500 placeholder-slate-700"
              />
            )}

            {/* Embedding 模式提示 */}
            {embedding && (
              <div className="flex items-center gap-2 px-3 py-2 bg-purple-500/10 border border-purple-500/20 rounded-lg">
                <CheckCircle2 className="w-3 h-3 text-purple-400 shrink-0" />
                <span className="text-[11px] font-mono text-purple-400">
                  已使用估值 Embedding（{embedding.length}维），跳过向量化步骤
                </span>
              </div>
            )}
          </div>

          {/* 错误提示 */}
          {error && (
            <div className="flex items-start gap-2 px-3 py-2.5 bg-red-500/10 border border-red-500/30 rounded-lg">
              <AlertTriangle className="w-3.5 h-3.5 text-red-400 mt-0.5 shrink-0" />
              <span className="text-xs font-mono text-red-400">{error}</span>
            </div>
          )}

          {/* 结果区 */}
          {report && vcfg && (
            <div className="space-y-4">

              {/* 判决卡 */}
              <div className={`rounded-xl border ${vcfg.bg} ${vcfg.border} p-4`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <VIcon className={`w-8 h-8 ${vcfg.iconColor}`} />
                    <div>
                      <div className={`text-sm font-mono font-black ${vcfg.iconColor}`}>
                        {vcfg.label}
                      </div>
                      <div className="text-[11px] font-mono text-slate-500 mt-0.5">
                        {vcfg.sublabel}
                      </div>
                    </div>
                  </div>
                  {/* 风险分 */}
                  <div className="text-right shrink-0">
                    <div className={`text-2xl font-black font-mono ${vcfg.iconColor}`}>
                      {Math.round(report.risk_score * 100)}
                    </div>
                    <div className="text-[9px] font-mono text-slate-600">风险分 /100</div>
                  </div>
                </div>

                {/* 消息 */}
                <div className="mt-3 text-[11px] font-mono text-slate-400 leading-relaxed">
                  {report.message}
                </div>

                {/* 统计小标签 */}
                <div className="mt-3 flex flex-wrap gap-2">
                  {[
                    { k: '碰撞', v: report.collision_count, c: 'text-red-400' },
                    { k: '警告', v: report.warning_count,   c: 'text-amber-400' },
                    { k: '检索范围', v: `${report.total_checked} 条`, c: 'text-slate-400' },
                    { k: '耗时', v: `${report.latency_ms?.toFixed(1)}ms`, c: 'text-slate-500' },
                  ].map(({ k, v, c }) => (
                    <div key={k} className="px-2 py-1 bg-slate-900/60 rounded-md text-[10px] font-mono">
                      <span className="text-slate-600">{k} </span>
                      <span className={`font-bold ${c}`}>{v}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Top-K 相似资产列表 */}
              {report.top_matches?.length > 0 && (
                <div className="space-y-2">
                  <div className="text-[10px] font-mono text-slate-600 uppercase tracking-widest px-1">
                    相似资产列表（点击展开详情）
                  </div>
                  {report.top_matches.map((match, i) => (
                    <MatchCard key={match.asset_hash + i} match={match} rank={i + 1} />
                  ))}
                </div>
              )}

              {/* 空库提示 */}
              {report.verdict === 'EMPTY_CORPUS' && (
                <div className="text-center py-6 text-slate-600 text-xs font-mono">
                  向量库中暂无其他资产，无法进行对比检索
                </div>
              )}
            </div>
          )}

          {/* 初始空状态 */}
          {!report && !loading && !error && (
            <div className="text-center py-8">
              <Shield className="w-10 h-10 text-slate-800 mx-auto mb-3" />
              <div className="text-xs font-mono text-slate-600">
                输入资产描述后点击「开始检测」<br />
                系统将在向量库中检索最相似的 {topK} 个资产
              </div>
            </div>
          )}

        </div>
      </div>
    </div>
  );
}
