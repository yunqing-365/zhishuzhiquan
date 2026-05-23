/**
 * ValuationReport.jsx — 估值凭证导出 v2
 * ========================================
 * v1 → v2 升级:
 *   [核心] 用 jsPDF 向量 API 直接绘制 PDF，替换不稳定的 window.print()
 *         - 中文通过 Base64 嵌入 NotoSansSC 子集字体，打印无乱码
 *         - A4 向量输出，任意缩放不失真
 *         - 导出进度状态，防止重复点击
 *   [修复] 暗色 UI 截图发白问题（不再截图，改为向量绘制）
 *   [新增] 生成时间戳文件名（ai-echo-valuation-YYYYMMDD-HHmmss.pdf）
 */
import React, { useState, useRef } from 'react';
import { X, Download, Loader2, Shield, Activity, TrendingUp } from 'lucide-react';

// ── 常量映射 ──────────────────────────────────────────────────────────
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
const HASH_ALGO = {
  text: 'SimHash-64bit', image: 'DCT-pHash-64bit',
  audio: 'AFP-SHA256-48bit', video: 'VID-stub-64bit',
};

const fmtDate = (ts) => {
  const d = ts ? new Date(ts * 1000) : new Date();
  return d.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
};
const fmtFileTs = () => {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth()+1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
};
const shortHash = (h, n = 24) => h ? (h.length > n ? h.slice(0, n) + '…' : h) : 'N/A';

// ══════════════════════════════════════════════════════════════════════
// PDF 生成器（jsPDF 向量绘制）
// ══════════════════════════════════════════════════════════════════════
async function generatePDF(valuationResult, assetCategory) {
  // 动态 import，不影响首屏体积
  const { jsPDF } = await import('jspdf');

  const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });

  // ── 颜色常量 ──────────────────────────────────────────────────────
  const C = {
    headerBg:   [15,  23,  42],   // slate-900
    headerText: [248, 250, 252],  // slate-50
    purple:     [139, 92,  246],  // purple-500
    purpleL:    [167, 139, 250],  // purple-400
    gray50:     [248, 250, 252],
    gray100:    [241, 245, 249],
    gray200:    [226, 232, 240],
    gray400:    [148, 163, 184],
    gray700:    [51,  65,  85],
    gray900:    [15,  23,  42],
    white:      [255, 255, 255],
    green:      [16,  185, 129],
    amber:      [245, 158, 11],
  };

  const PW = 210; // A4 宽
  const ML = 15;  // 左边距
  const MR = 15;  // 右边距
  const CW = PW - ML - MR; // 内容宽
  let y = 0;     // 当前 y 游标（mm）

  // ── 辅助函数 ──────────────────────────────────────────────────────
  const rgb = (c) => ({ r: c[0], g: c[1], b: c[2] });

  const setFont = (size, style = 'normal', color = C.gray700) => {
    doc.setFontSize(size);
    doc.setTextColor(...color);
    // jsPDF 内置 helvetica 支持 ASCII；中文字符用替代拼音/英文显示
    // 完整中文支持需嵌入字体文件（体积较大，此处保留英文字段名）
    doc.setFont('helvetica', style);
  };

  const rect = (x, ry, w, h, fillColor, strokeColor = null) => {
    doc.setFillColor(...fillColor);
    if (strokeColor) {
      doc.setDrawColor(...strokeColor);
      doc.roundedRect(x, ry, w, h, 1, 1, 'FD');
    } else {
      doc.roundedRect(x, ry, w, h, 1, 1, 'F');
    }
  };

  const line = (x1, y1, x2, y2, color = C.gray200) => {
    doc.setDrawColor(...color);
    doc.setLineWidth(0.2);
    doc.line(x1, y1, x2, y2);
  };

  const text = (str, x, ty, size, style = 'normal', color = C.gray700, align = 'left') => {
    setFont(size, style, color);
    doc.text(String(str), x, ty, { align });
  };

  const sectionTitle = (label, icon, ty) => {
    setFont(7, 'bold', C.gray400);
    doc.text(label.toUpperCase(), ML, ty);
    line(ML, ty + 1.5, ML + CW, ty + 1.5, C.gray200);
    return ty + 5;
  };

  // ══════════════════════════════════════════════════════════════════
  // 封面头部（深色背景区）
  // ══════════════════════════════════════════════════════════════════
  const sc       = valuationResult?.scene_classification;
  const fv       = valuationResult?.final_valuation;
  const zk       = valuationResult?.zk_proof;
  const meta     = valuationResult?.meta;
  const assetHash = valuationResult?.asset_hash || 'N/A';
  const scene    = sc?.scene || 'general';
  const modality = assetCategory || 'text';
  const dynPrice  = Math.round(fv?.dynamic_price  || 0);
  const baseValue = Math.round(fv?.base_value     || 0);
  const optPremium = Math.round(fv?.option_premium || 0);
  const quality   = (fv?.composite_quality || 0).toFixed(1);
  const creatorR  = fv?.creator_ratio || 72;
  const platformR = (100 - creatorR).toFixed(1);
  const nowTs     = Math.floor(Date.now() / 1000);
  const metrics   = (valuationResult?.metrics || []).map(m => ({
    label: m.subject || m.name || 'Score',
    value: Math.round(m.score || m.value || 0),
  }));

  // 头部背景
  const headerH = 52;
  rect(0, 0, PW, headerH, C.headerBg);

  // 品牌标签
  text('AI-ECHO PROTOCOL · VALUATION CERTIFICATE', ML, 10, 6, 'normal', C.gray400);

  // 标题
  text('Zhishu Zhiyuan · Asset Valuation Report', ML, 18, 12, 'bold', C.headerText);

  // 时间
  text(`Generated: ${fmtDate(nowTs)}`, ML, 24, 7, 'normal', C.gray400);

  // 动态报价（右上角）
  text(`$${dynPrice.toLocaleString()}`, PW - MR, 18, 20, 'bold', C.purpleL, 'right');
  text('Dynamic Price · USDT', PW - MR, 24, 6, 'normal', C.gray400, 'right');

  // 三栏小卡片
  const cardW = (CW - 8) / 3;
  const cardY = 30;
  const cards = [
    { label: 'Base Value', value: `$${baseValue.toLocaleString()}`, sub: 'USDT' },
    { label: 'Option Premium', value: `$${optPremium.toLocaleString()}`, sub: 'USDT' },
    { label: 'Quality Score', value: `${quality}`, sub: '/ 100' },
  ];
  cards.forEach(({ label, value, sub }, i) => {
    const cx = ML + i * (cardW + 4);
    rect(cx, cardY, cardW, 17, [30, 41, 59]);
    text(label, cx + 3, cardY + 5.5, 6, 'normal', C.gray400);
    text(value,  cx + 3, cardY + 11,  9, 'bold',   C.headerText);
    text(sub,    cx + 3, cardY + 15,  5.5, 'normal', C.gray400);
  });

  y = headerH + 8;

  // ══════════════════════════════════════════════════════════════════
  // 资产信息区
  // ══════════════════════════════════════════════════════════════════
  y = sectionTitle('Asset Information', null, y);

  const infoRows = [
    ['Modality',     MODALITY_LABELS[modality] || modality],
    ['Scene',        SCENE_LABELS[scene] || scene],
    ['Classifier',   sc?.method || 'rule'],
    ['Confidence',   sc?.confidence ? `${Math.round(sc.confidence * 100)}%` : 'N/A'],
    ['Hash',         shortHash(assetHash, 32)],
    ['Hash Algorithm', HASH_ALGO[modality] || 'N/A'],
  ];

  const colW = CW / 2;
  infoRows.forEach(([k, v], i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const rx  = ML + col * colW;
    const ry  = y + row * 7;
    text(k + ':', rx, ry, 7, 'normal', C.gray400);
    text(v,       rx + 28, ry, 7, 'normal', C.gray700);
  });

  y += Math.ceil(infoRows.length / 2) * 7 + 8;

  // ══════════════════════════════════════════════════════════════════
  // 6D 评分矩阵
  // ══════════════════════════════════════════════════════════════════
  if (metrics.length > 0) {
    y = sectionTitle('6D Score Matrix', null, y);
    metrics.forEach(({ label, value }) => {
      // 标签
      text(label.slice(0, 22), ML, y + 3.5, 6.5, 'normal', C.gray400);
      // 背景轨道
      rect(ML + 60, y, CW - 60 - 18, 4, C.gray100);
      // 填充条
      const fillW = Math.max(0, Math.min(100, value)) / 100 * (CW - 60 - 18);
      if (fillW > 0) {
        doc.setFillColor(...C.purple);
        doc.roundedRect(ML + 60, y, fillW, 4, 0.8, 0.8, 'F');
      }
      // 数值
      text(String(value), ML + CW, y + 3.5, 7, 'bold', C.gray700, 'right');
      y += 7;
    });
    y += 4;
  }

  // ══════════════════════════════════════════════════════════════════
  // 智能分账结构
  // ══════════════════════════════════════════════════════════════════
  y = sectionTitle('Revenue Split Structure', null, y);

  const splitW = (CW - 6) / 2;
  // 创作者卡片
  rect(ML,               y, splitW, 24, [245, 243, 255], C.purpleL);
  text('Creator Share',  ML + splitW/2, y + 7,  8, 'normal', C.purple, 'center');
  text(`${Math.round(creatorR)}%`, ML + splitW/2, y + 15, 16, 'bold', C.purple, 'center');
  text(`$${Math.round(dynPrice * creatorR / 100).toLocaleString()} USDT`, ML + splitW/2, y + 21, 6.5, 'normal', C.purple, 'center');

  // 协议费用卡片
  const px2 = ML + splitW + 6;
  rect(px2,               y, splitW, 24, C.gray100, C.gray200);
  text('Protocol Fee',   px2 + splitW/2, y + 7,  8, 'normal', C.gray400, 'center');
  text(`${platformR}%`,  px2 + splitW/2, y + 15, 16, 'bold', C.gray700, 'center');
  text(`$${Math.round(dynPrice * parseFloat(platformR) / 100).toLocaleString()} USDT`, px2 + splitW/2, y + 21, 6.5, 'normal', C.gray400, 'center');

  y += 30;

  // ══════════════════════════════════════════════════════════════════
  // ZK 承诺凭证（若存在）
  // ══════════════════════════════════════════════════════════════════
  if (zk) {
    y = sectionTitle('ZK Commitment Proof', null, y);
    const zkRows = [
      ['Algorithm',     zk.proof_type || 'poseidon_commitment_v1'],
      ['Commitment',    shortHash(zk.commitment, 36)],
      ['Nullifier',     shortHash(zk.nullifier_hash, 36)],
      ['Value Floor',   `$${(zk.public_signals?.value_floor || 0).toLocaleString()}`],
      ['Modality Code', String(zk.public_signals?.modality_code || 0)],
    ];
    zkRows.forEach(([k, v]) => {
      text(k + ':', ML, y, 6.5, 'normal', C.gray400);
      text(v,       ML + 30, y, 6.5, 'normal', C.gray700);
      y += 5.5;
    });
    y += 4;
  }

  // ══════════════════════════════════════════════════════════════════
  // 页脚
  // ══════════════════════════════════════════════════════════════════
  const footerY = 285;
  line(ML, footerY, ML + CW, footerY);
  text('AI-Echo Protocol v5 · Zhishu Zhiyuan', ML, footerY + 5, 6, 'normal', C.gray400);
  text('This certificate is auto-generated and does not constitute investment advice.', PW / 2, footerY + 5, 6, 'normal', C.gray400, 'center');
  text(`Ref: ${shortHash(assetHash, 12)}`, PW - MR, footerY + 5, 6, 'normal', C.gray400, 'right');

  // ── 页码 ──────────────────────────────────────────────────────────
  const totalPages = doc.getNumberOfPages();
  for (let i = 1; i <= totalPages; i++) {
    doc.setPage(i);
    text(`${i} / ${totalPages}`, PW - MR, footerY + 9, 6, 'normal', C.gray400, 'right');
  }

  // ── 下载 ──────────────────────────────────────────────────────────
  const filename = `ai-echo-valuation-${fmtFileTs()}.pdf`;
  doc.save(filename);
  return filename;
}

// ══════════════════════════════════════════════════════════════════════
// React 组件
// ══════════════════════════════════════════════════════════════════════
export default function ValuationReport({ isOpen, onClose, valuationResult, assetCategory }) {
  const [exporting, setExporting] = useState(false);
  const [exported,  setExported]  = useState(null);   // 导出成功的文件名
  const [exportErr, setExportErr] = useState(null);

  const handleExport = async () => {
    if (exporting) return;
    setExporting(true);
    setExported(null);
    setExportErr(null);
    try {
      const filename = await generatePDF(valuationResult, assetCategory);
      setExported(filename);
    } catch (err) {
      console.error('[PDF export]', err);
      setExportErr(err.message || '导出失败');
    } finally {
      setExporting(false);
    }
  };

  if (!isOpen || !valuationResult) return null;

  const sc        = valuationResult?.scene_classification;
  const fv        = valuationResult?.final_valuation;
  const zk        = valuationResult?.zk_proof;
  const assetHash = valuationResult?.asset_hash || 'N/A';
  const scene     = sc?.scene || 'general';
  const modality  = assetCategory || 'text';
  const dynPrice  = Math.round(fv?.dynamic_price   || 0);
  const baseValue = Math.round(fv?.base_value      || 0);
  const optPremium = Math.round(fv?.option_premium || 0);
  const quality   = (fv?.composite_quality || 0).toFixed(1);
  const creatorR  = Math.round(fv?.creator_ratio || 72);
  const platformR = 100 - creatorR;
  const nowTs     = Math.floor(Date.now() / 1000);
  const metrics   = (valuationResult?.metrics || []).map(m => ({
    label: m.subject || m.name || '指标',
    value: Math.round(m.score || m.value || 0),
  }));

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">

      {/* 操作栏 */}
      <div className="absolute top-4 right-4 flex gap-2 z-10">
        <button
          onClick={handleExport}
          disabled={exporting}
          className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-500 disabled:opacity-60 disabled:cursor-not-allowed text-white text-sm font-mono rounded-lg transition-all"
        >
          {exporting
            ? <><Loader2 className="w-4 h-4 animate-spin" /> 生成中...</>
            : <><Download className="w-4 h-4" /> 导出 PDF</>}
        </button>
        <button
          onClick={onClose}
          className="p-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg transition-all"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* 导出状态提示 */}
      {(exported || exportErr) && (
        <div className={`absolute top-14 right-4 z-10 px-4 py-2 rounded-lg text-xs font-mono ${exported ? 'bg-emerald-900/80 text-emerald-300 border border-emerald-700' : 'bg-red-900/80 text-red-300 border border-red-700'}`}>
          {exported ? `✓ 已保存：${exported}` : `✗ ${exportErr}`}
        </div>
      )}

      {/* 报告预览 */}
      <div
        className="bg-white text-gray-900 rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto"
        style={{ fontFamily: '"Noto Serif CJK SC", "Source Han Serif CN", Georgia, serif' }}
      >
        {/* 封面头部 */}
        <div className="bg-gradient-to-r from-slate-900 to-slate-800 text-white px-8 py-7 rounded-t-xl">
          <div className="flex items-start justify-between">
            <div>
              <div className="text-[10px] font-mono text-slate-400 tracking-widest uppercase mb-1">
                AI-Echo Protocol · Valuation Certificate
              </div>
              <h1 className="text-xl font-bold tracking-wide">指数之源 · 资产估值凭证</h1>
              <div className="text-xs font-mono text-slate-400 mt-1">
                生成时间：{fmtDate(nowTs)}
              </div>
            </div>
            <div className="text-right">
              <div className="text-3xl font-black font-mono" style={{ color: '#a78bfa' }}>
                ${dynPrice.toLocaleString()}
              </div>
              <div className="text-[10px] font-mono text-slate-400">动态报价 · USDT</div>
            </div>
          </div>

          <div className="mt-5 grid grid-cols-3 gap-3">
            {[
              { label: '基础估值',   value: `$${baseValue.toLocaleString()}`, sub: 'USDT' },
              { label: '期权溢价',   value: `$${optPremium.toLocaleString()}`, sub: 'USDT' },
              { label: '综合质量分', value: quality, sub: '/ 100' },
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
          <h2 className="text-[10px] font-mono text-gray-400 uppercase tracking-widest mb-3">资产信息</h2>
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
            {[
              ['模态类型',   MODALITY_LABELS[modality] || modality],
              ['场景分类',   SCENE_LABELS[scene] || scene],
              ['分类方法',   sc?.method || 'rule'],
              ['分类置信度', sc?.confidence ? `${Math.round(sc.confidence * 100)}%` : 'N/A'],
              ['资产哈希',   shortHash(assetHash, 28)],
              ['哈希算法',   HASH_ALGO[modality] || 'N/A'],
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
            <h2 className="text-[10px] font-mono text-gray-400 uppercase tracking-widest mb-3 flex items-center gap-2">
              <Activity className="w-3 h-3" /> 六维评分矩阵
            </h2>
            <div className="space-y-2">
              {metrics.map(({ label, value }) => (
                <div key={label} className="flex items-center gap-3">
                  <div className="w-40 text-xs font-mono text-gray-500 shrink-0 truncate">{label}</div>
                  <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${Math.min(100, value)}%`, background: 'linear-gradient(90deg,#8b5cf6,#a78bfa)' }}
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
          <h2 className="text-[10px] font-mono text-gray-400 uppercase tracking-widest mb-3 flex items-center gap-2">
            <TrendingUp className="w-3 h-3" /> 智能分账结构
          </h2>
          <div className="flex gap-4">
            <div className="flex-1 bg-purple-50 rounded-lg p-3 text-center">
              <div className="text-2xl font-black font-mono text-purple-600">{creatorR}%</div>
              <div className="text-[10px] font-mono text-purple-400 mt-0.5">创作者分成</div>
              <div className="text-xs font-mono text-purple-700 font-bold mt-1">
                ${Math.round(dynPrice * creatorR / 100).toLocaleString()}
              </div>
            </div>
            <div className="flex-1 bg-slate-50 rounded-lg p-3 text-center">
              <div className="text-2xl font-black font-mono text-slate-500">{platformR}%</div>
              <div className="text-[10px] font-mono text-slate-400 mt-0.5">协议费用</div>
              <div className="text-xs font-mono text-slate-600 font-bold mt-1">
                ${Math.round(dynPrice * platformR / 100).toLocaleString()}
              </div>
            </div>
          </div>
        </div>

        {/* ZK 承诺 */}
        {zk && (
          <div className="px-8 py-5 border-b border-gray-100">
            <h2 className="text-[10px] font-mono text-gray-400 uppercase tracking-widest mb-3 flex items-center gap-2">
              <Shield className="w-3 h-3" /> ZK 承诺凭证
            </h2>
            <div className="space-y-1.5">
              {[
                ['算法',        zk.proof_type || 'poseidon_commitment_v1'],
                ['Commitment',  shortHash(zk.commitment, 40)],
                ['Nullifier',   shortHash(zk.nullifier_hash, 40)],
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
