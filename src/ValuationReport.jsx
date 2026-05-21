/**
 * ValuationReport.jsx — 估值凭证导出 v1
 * ========================================
 * 在 SmartSplitScreen 点击"导出报告"后渲染此组件并触发 window.print()。
 * 使用浏览器内置打印（Ctrl+P / Cmd+P → 另存为 PDF），零额外依赖。
 * 包含完整的 @media print CSS，打印时隐藏所有导航元素，只输出报告内容。
 */
import React, { useEffect, useRef } from 'react';
import { X, Download, Shield, Hash, Activity, TrendingUp, Zap } from 'lucide-react';

const SCENE_LABELS = {
  medical_sft: '医疗 SFT', legal_doc: '法律文书', code_tech: '代码技术',
  creative: '创意写作', chat_qa: '问答对话', illustration: '原创插画',
  photo: '摄影作品', diagram: '图表图解', screenshot: '截图素材',
  speech_medical: '医疗语音', speech_legal: '法律音频', speech_edu: '教育语音',
  music_original: '原创音乐', ambient_sfx: '环境音效', general: '通用',
  documentary: '纪录/访谈', lecture: '教学讲解', cinematic: '影视创作',
  sports_action: '运动/动作', vlog: '个人 vlog', noise: '噪声',
};

const MODALITY_LABELS = { text: '文本', image: '图像', audio: '音频', video: '视频' };

// 格式化时间戳
const fmtDate = (ts) => {
  const d = ts ? new Date(ts * 1000) : new Date();
  return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
};

// 截断哈希
const shortHash = (h, n = 20) => h ? (h.length > n ? h.slice(0, n) + '...' : h) : 'N/A';

export default function ValuationReport({ isOpen, onClose, valuationResult, assetCategory }) {
  const printRef = useRef(null);

  useEffect(() => {
    if (!isOpen) return;
    // 注入打印样式（仅在打印时生效）
    const styleId = 'ai-echo-print-style';
    if (!document.getElementById(styleId)) {
      const style = document.createElement('style');
      style.id = styleId;
      style.textContent = `
        @media print {
          body > *:not(#ai-echo-report-root) { display: none !important; }
          #ai-echo-report-root { display: block !important; position: fixed; inset: 0; z-index: 9999; }
          .no-print { display: none !important; }
          .print-page { page-break-after: always; }
          @page { margin: 15mm; size: A4; }
        }
      `;
      document.head.appendChild(style);
    }
  }, [isOpen]);

  const handlePrint = () => window.print();

  if (!isOpen || !valuationResult) return null;

  const sc  = valuationResult?.scene_classification;
  const fv  = valuationResult?.final_valuation;
  const zk  = valuationResult?.zk_proof;
  const meta = valuationResult?.meta;
  const assetHash = valuationResult?.asset_hash || 'N/A';
  const scene = sc?.scene || 'general';
  const modality = assetCategory || 'text';
  const baseValue = Math.round(fv?.base_value || 0);
  const dynPrice  = Math.round(fv?.dynamic_price || 0);
  const optPremium = Math.round(fv?.option_premium || 0);
  const quality   = Math.round((fv?.composite_quality || 0) * 100);
  const creatorRatio = Math.round((fv?.creator_ratio || 0.7) * 100);
  const platformRatio = 100 - creatorRatio;
  const nowTs = Math.floor(Date.now() / 1000);

  // 6D 指标
  const metrics = (valuationResult?.metrics || []).map(m => ({
    label: m.subject || m.name || '指标',
    value: Math.round(m.score || m.value || 0),
  }));

  return (
    <div
      id="ai-echo-report-root"
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
    >
      {/* ── 控制栏（打印时隐藏）── */}
      <div className="no-print absolute top-4 right-4 flex gap-2 z-10">
        <button
          onClick={handlePrint}
          className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white text-sm font-mono rounded-lg transition-all"
        >
          <Download className="w-4 h-4" />
          导出 PDF
        </button>
        <button
          onClick={onClose}
          className="p-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg transition-all"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* ── 报告主体（可滚动预览，打印时铺满页面）── */}
      <div
        ref={printRef}
        className="bg-white text-gray-900 rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto"
        style={{ fontFamily: '"Source Han Serif CN", "Noto Serif CJK SC", Georgia, serif' }}
      >
        {/* 封面头部 */}
        <div className="bg-gradient-to-r from-slate-900 to-slate-800 text-white px-8 py-7 rounded-t-xl">
          <div className="flex items-start justify-between">
            <div>
              <div className="text-[10px] font-mono text-slate-400 tracking-widest uppercase mb-1">
                AI-Echo Protocol · Valuation Certificate
              </div>
              <h1 className="text-xl font-bold tracking-wide">
                指数之源 · 资产估值凭证
              </h1>
              <div className="text-xs font-mono text-slate-400 mt-1">
                生成时间：{fmtDate(nowTs)}
              </div>
            </div>
            <div className="text-right">
              <div
                className="text-3xl font-black font-mono"
                style={{ color: '#a78bfa' }}
              >
                ${dynPrice.toLocaleString()}
              </div>
              <div className="text-[10px] font-mono text-slate-400">动态报价 · USDT</div>
            </div>
          </div>

          {/* 进度条形指标 */}
          <div className="mt-5 grid grid-cols-3 gap-3">
            {[
              { label: '基础估值', value: `$${baseValue.toLocaleString()}`, sub: 'USDT' },
              { label: '期权溢价', value: `$${optPremium.toLocaleString()}`, sub: 'USDT' },
              { label: '综合质量分', value: `${quality}%`, sub: 'Composite Score' },
            ].map(({ label, value, sub }) => (
              <div key={label} className="bg-slate-800/60 rounded-lg px-3 py-2">
                <div className="text-[10px] text-slate-400 font-mono">{label}</div>
                <div className="text-base font-bold font-mono text-white">{value}</div>
                <div className="text-[9px] text-slate-500 font-mono">{sub}</div>
              </div>
            ))}
          </div>
        </div>

        {/* 资产信息 */}
        <div className="px-8 py-5 border-b border-gray-100">
          <h2 className="text-xs font-mono text-gray-400 uppercase tracking-widest mb-3">资产信息</h2>
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
            {[
              ['模态类型',   MODALITY_LABELS[modality] || modality],
              ['场景分类',   SCENE_LABELS[scene] || scene],
              ['分类方法',   sc?.method || 'rule'],
              ['分类置信度', sc?.confidence ? `${Math.round(sc.confidence * 100)}%` : 'N/A'],
              ['资产哈希',   shortHash(assetHash, 28)],
              ['哈希算法',   modality === 'text' ? 'SimHash-64bit' : modality === 'image' ? 'DCT-pHash-64bit' : modality === 'audio' ? 'AFP-SHA256-48bit' : 'VID-stub-64bit'],
            ].map(([k, v]) => (
              <div key={k} className="flex gap-2">
                <span className="text-gray-400 font-mono text-xs w-20 shrink-0">{k}</span>
                <span className="text-gray-800 font-mono text-xs break-all">{v}</span>
              </div>
            ))}
          </div>
        </div>

        {/* 6D 评分 */}
        {metrics.length > 0 && (
          <div className="px-8 py-5 border-b border-gray-100">
            <h2 className="text-xs font-mono text-gray-400 uppercase tracking-widest mb-3 flex items-center gap-2">
              <Activity className="w-3 h-3" /> 六维评分矩阵
            </h2>
            <div className="space-y-2">
              {metrics.map(({ label, value }) => (
                <div key={label} className="flex items-center gap-3">
                  <div className="w-36 text-xs font-mono text-gray-500 shrink-0 truncate">{label}</div>
                  <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${Math.min(100, value)}%`, background: 'linear-gradient(90deg, #8b5cf6, #a78bfa)' }}
                    />
                  </div>
                  <div className="text-xs font-mono text-gray-700 w-8 text-right font-bold">{value}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 分账结构 */}
        <div className="px-8 py-5 border-b border-gray-100">
          <h2 className="text-xs font-mono text-gray-400 uppercase tracking-widest mb-3 flex items-center gap-2">
            <TrendingUp className="w-3 h-3" /> 智能分账结构
          </h2>
          <div className="flex gap-4">
            <div className="flex-1 bg-purple-50 rounded-lg p-3 text-center">
              <div className="text-2xl font-black font-mono text-purple-600">{creatorRatio}%</div>
              <div className="text-[10px] font-mono text-purple-400 mt-0.5">创作者分成</div>
              <div className="text-xs font-mono text-purple-700 font-bold mt-1">${Math.round(dynPrice * creatorRatio / 100).toLocaleString()}</div>
            </div>
            <div className="flex-1 bg-slate-50 rounded-lg p-3 text-center">
              <div className="text-2xl font-black font-mono text-slate-500">{platformRatio}%</div>
              <div className="text-[10px] font-mono text-slate-400 mt-0.5">协议费用</div>
              <div className="text-xs font-mono text-slate-600 font-bold mt-1">${Math.round(dynPrice * platformRatio / 100).toLocaleString()}</div>
            </div>
          </div>
        </div>

        {/* ZK 承诺 */}
        {zk && (
          <div className="px-8 py-5 border-b border-gray-100">
            <h2 className="text-xs font-mono text-gray-400 uppercase tracking-widest mb-3 flex items-center gap-2">
              <Shield className="w-3 h-3" /> ZK 承诺凭证
            </h2>
            <div className="space-y-1.5">
              {[
                ['算法',      zk.proof_type || 'poseidon_commitment_v1'],
                ['Commitment', shortHash(zk.commitment, 40)],
                ['Nullifier', shortHash(zk.nullifier_hash, 40)],
                ['Value Floor', `$${(zk.public_signals?.value_floor || 0).toLocaleString()}`],
                ['Modality Code', String(zk.public_signals?.modality_code || 0)],
              ].map(([k, v]) => (
                <div key={k} className="flex gap-2 text-xs font-mono">
                  <span className="text-gray-400 w-24 shrink-0">{k}</span>
                  <span className="text-gray-700 break-all">{v}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 页脚 */}
        <div className="px-8 py-4 bg-gray-50 rounded-b-xl">
          <div className="flex items-center justify-between text-[10px] font-mono text-gray-400">
            <span>AI-Echo Protocol v5 · 指数之源</span>
            <span>本凭证由去中心化预言机自动生成，不构成投资建议</span>
            <span>Ref: {shortHash(assetHash, 12)}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
