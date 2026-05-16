import React, { useState, useEffect } from 'react';
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer, Tooltip } from 'recharts';
import { Activity, ShieldCheck, FileText, Network, CheckCircle2, ArrowRight, Tag, Layers, FlaskConical } from 'lucide-react';

// 场景标签配置 (与后端 scene_classifier.py 保持同步)
const SCENE_LABELS = {
  medical_sft:  { label: '医疗 SFT',  color: 'text-red-400',    bg: 'bg-red-900/20 border-red-500/30',       mult: '1.35×' },
  legal_doc:    { label: '法律文书',  color: 'text-orange-400', bg: 'bg-orange-900/20 border-orange-500/30', mult: '1.20×' },
  code_tech:    { label: '代码技术',  color: 'text-cyan-400',   bg: 'bg-cyan-900/20 border-cyan-500/30',     mult: '1.10×' },
  creative:     { label: '创意写作',  color: 'text-pink-400',   bg: 'bg-pink-900/20 border-pink-500/30',     mult: '0.90×' },
  chat_qa:      { label: '问答对话',  color: 'text-blue-400',   bg: 'bg-blue-900/20 border-blue-500/30',     mult: '0.80×' },
  illustration: { label: '原创插画',  color: 'text-amber-400',  bg: 'bg-amber-900/20 border-amber-500/30',   mult: '1.50×' },
  photo:        { label: '摄影作品',  color: 'text-yellow-400', bg: 'bg-yellow-900/20 border-yellow-500/30', mult: '1.00×' },
  screenshot:   { label: '截图素材',  color: 'text-slate-400',  bg: 'bg-slate-900/30 border-slate-600/30',   mult: '0.25×' },
  diagram:      { label: '图表图解',  color: 'text-violet-400', bg: 'bg-violet-900/20 border-violet-500/30', mult: '0.55×' },
  noise:        { label: '噪声/废话', color: 'text-red-500',    bg: 'bg-red-950/30 border-red-700/30',       mult: '0.05×' },
};

// ★ v3: 接收 sceneOverride prop
const OracleValuationScreen = ({ assetData, assetCategory, isZkMode, sceneOverride, onNext }) => {
  const [isCalculating, setIsCalculating] = useState(true);
  const [calcStep, setCalcStep]           = useState(0);
  const [chartData, setChartData]         = useState([]);
  const [valuationResult, setValuationResult] = useState(null);
  // ★ v3: 记录后端实际使用的分类方法 (rule / ml / hybrid)
  const [classifyMethod, setClassifyMethod] = useState(null);

  const defaultMetricNames = {
    image: ['CLIP语义对齐度', '频域隐写鲁棒性(DWT)', 'LAION美学评级', '画派风格稀缺度', 'LoRA微调增益', 'KNN-Shapley贡献度'],
    text:  ['信息熵密度(抗废话)', '场景信噪比', '实体拓扑密度(GraphRAG)', '语料库稀缺度', '大模型微调增益', 'KNN-Shapley贡献度'],
  };

  // ★ v3: Stage 2 日志反映是否为覆盖模式
  const EXEC_STEPS = [
    `[Stage 1] 接收 [${assetCategory.toUpperCase()}-ADAPTER] 降维映射特征流...`,
    sceneOverride
      ? `[Stage 2] 场景覆盖已激活 → 跳过 SceneClassifier，强制使用场景: ${sceneOverride}`
      : '[Stage 2] 调用 SceneClassifier v3 → hybrid 规则+ML 引擎识别场景子类型...',
    '[Stage 3] 按场景路径提取专项指标 (废话熔断 / 代码结构 / 美学打分)...',
    '[Stage 4] 场景自适应权重向量 × TEV 双层乘数 → 跨模态复合评分...',
    '[Stage 5] AMM 联合曲线 + KNN-Shapley + 实物期权 → 统一定价上链...',
  ];

  useEffect(() => {
    const names = defaultMetricNames[assetCategory] || defaultMetricNames.text;
    setChartData(names.map(n => ({ subject: n, score: 0, fullMark: 100 })));

    let cur = 0;
    const ticker = setInterval(() => {
      if (cur < EXEC_STEPS.length) { setCalcStep(cur); cur++; }
    }, 750);

    const fetchValuation = async () => {
      try {
        const res = await fetch('http://127.0.0.1:8000/api/valuate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            asset_category: assetCategory,
            description:    assetData,
            is_zk_mode:     isZkMode,
            scene_override: sceneOverride ?? null,   // ★ v3: 传递场景覆盖
          }),
        });
        if (!res.ok) throw new Error('HTTP Error');
        const data = await res.json();
        // ★ v3: 读取后端返回的分类方法
        setClassifyMethod(data.scene_classification?.method ?? null);
        setTimeout(() => {
          clearInterval(ticker);
          setValuationResult(data);
          if (data.metrics) setChartData(data.metrics);
          setIsCalculating(false);
        }, 4200);
      } catch {
        // Mock fallback (API 未启动时)
        const isImg = assetCategory === 'image';
        // ★ v3: mock 时也尊重 sceneOverride
        const mockScene = sceneOverride || (isImg ? 'illustration' : 'medical_sft');
        const mockScores = isImg ? [92, 95, 96, 96, 89, 85] : [88, 82, 85, 93, 80, 91];
        const mockMetrics = names.map((n, i) => ({ subject: n, score: mockScores[i], fullMark: 100 }));
        setClassifyMethod(sceneOverride ? 'override' : 'rule');
        setTimeout(() => {
          clearInterval(ticker);
          setValuationResult({
            status: 'success',
            asset_hash: '0xMockDCT_A8F3B2C1D4E5...',
            scene_classification: {
              scene:        mockScene,
              confidence:   sceneOverride ? 1.0 : 0.87,
              quality_axis: isImg ? 'structure' : 'snr',
              method:       sceneOverride ? 'override' : 'rule',
            },
            metrics: mockMetrics,
            final_valuation: {
              composite_quality: 88.5,
              modality_tev:      isImg ? '50x' : '1x',
              scene_multiplier:  isImg ? '1.5x' : '1.35x',
              effective_weight:  isImg ? '75x' : '1.35x',
              base_value:        isImg ? 13275 : 239,
              dynamic_price:     isImg ? 16279 : 298,
              option_premium:    isImg ? 4012  : 68,
              sigma:             0.64,
              market_demand:     isImg ? 22 : 28,
              creator_ratio:     87.5,
            },
          });
          setChartData(mockMetrics);
          setIsCalculating(false);
        }, 4200);
      }
    };

    fetchValuation();
    return () => clearInterval(ticker);
  }, []);

  const sc          = valuationResult?.scene_classification;
  const sceneConfig = sc ? (SCENE_LABELS[sc.scene] || SCENE_LABELS['chat_qa']) : null;
  const fv          = valuationResult?.final_valuation;

  // ★ v3: 分类方法徽章颜色
  const methodBadge = {
    rule:     { label: 'rule',     cls: 'text-slate-400 border-slate-600/40 bg-slate-900/40' },
    ml:       { label: 'ML',       cls: 'text-cyan-400  border-cyan-500/40  bg-cyan-900/20'  },
    hybrid:   { label: 'hybrid',   cls: 'text-purple-400 border-purple-500/40 bg-purple-900/20' },
    override: { label: 'override', cls: 'text-amber-400 border-amber-500/40 bg-amber-900/20' },
  }[classifyMethod ?? sc?.method] ?? null;

  return (
    <div className="min-h-screen relative flex items-center justify-center bg-slate-950 p-6 font-sans">
      <div className="absolute top-0 left-0 w-[600px] h-[600px] bg-purple-900/10 rounded-full blur-[200px] pointer-events-none" />
      <div className="relative max-w-6xl w-full bg-slate-900/80 backdrop-blur-2xl border border-slate-700/50 rounded-3xl p-8 shadow-2xl flex flex-col" style={{ minHeight: '88vh' }}>

        {/* Header */}
        <div className="flex items-center justify-between mb-6 pb-4 border-b border-slate-700/50">
          <div className="flex flex-col gap-2">
            <div className="flex items-center space-x-3">
              <Network className="w-7 h-7 text-purple-400" />
              {/* ★ v3 标题 */}
              <h1 className="text-xl font-extrabold tracking-wide text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-indigo-300">
                多模态统一定价预言机 v3
              </h1>
              {/* ★ 场景覆盖激活提示 */}
              {sceneOverride && (
                <span className="flex items-center gap-1 px-2 py-0.5 rounded border border-amber-500/40 bg-amber-900/20 text-amber-300 text-[10px] font-mono">
                  <FlaskConical className="w-3 h-3" /> 调试·覆盖: {sceneOverride}
                </span>
              )}
            </div>

            {/* Pipeline breadcrumb */}
            <div className="flex items-center flex-wrap gap-1 text-[10px] font-mono tracking-wider">
              <span className={`px-2.5 py-1 rounded border ${assetCategory === 'image' ? 'bg-amber-900/20 text-amber-400 border-amber-500/30' : 'bg-blue-900/20 text-blue-400 border-blue-500/30'}`}>
                {assetCategory === 'image' ? 'Image-Adapter' : 'Text-Adapter'}
              </span>
              <ArrowRight className="w-3 h-3 text-slate-600" />
              {sc ? (
                <span className={`px-2.5 py-1 rounded border flex items-center gap-1 ${sceneConfig.bg} ${sceneConfig.color}`}>
                  <Tag className="w-3 h-3" /> {sceneConfig.label}
                  <span className="opacity-60 ml-1">{Math.round(sc.confidence * 100)}%</span>
                  {/* ★ v3: 分类方法徽章 */}
                  {methodBadge && (
                    <span className={`ml-1 px-1.5 py-0.5 rounded border text-[9px] font-bold ${methodBadge.cls}`}>
                      {methodBadge.label}
                    </span>
                  )}
                </span>
              ) : (
                <span className="px-2.5 py-1 rounded border bg-purple-900/20 text-purple-400 border-purple-500/30 animate-pulse">
                  {sceneOverride ? `强制: ${sceneOverride}` : 'Scene Classifying...'}
                </span>
              )}
              <ArrowRight className="w-3 h-3 text-slate-600" />
              <span className="px-2.5 py-1 rounded border bg-emerald-900/20 text-emerald-400 border-emerald-500/30">
                Unified TEV Pricing
              </span>
            </div>
          </div>

          <div className={`px-4 py-2 rounded-full border text-sm font-bold flex items-center ${isCalculating ? 'bg-amber-500/10 border-amber-500/30 text-amber-400' : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'}`}>
            {isCalculating ? <Activity className="w-4 h-4 mr-2 animate-spin" /> : <CheckCircle2 className="w-4 h-4 mr-2" />}
            {isCalculating ? 'Computing...' : 'Valuation Complete'}
          </div>
        </div>

        {/* Main grid */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 flex-1">

          {/* Left: Radar chart */}
          <div className="bg-slate-950/50 rounded-2xl border border-slate-800 p-6 flex flex-col">
            <h3 className="text-xs font-bold uppercase tracking-widest mb-4 flex items-center text-purple-400">
              <Activity className="w-4 h-4 mr-2" /> 场景自适应 6D 特征矩阵
            </h3>
            <div className="flex-1 w-full" style={{ minHeight: 260 }}>
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart cx="50%" cy="50%" outerRadius="68%" data={chartData}>
                  <PolarGrid stroke="#1e293b" />
                  <PolarAngleAxis dataKey="subject" tick={{ fill: '#94a3b8', fontSize: 10, fontWeight: 600 }} />
                  <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} axisLine={false} />
                  <Tooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: '#8b5cf6', borderRadius: '8px' }} itemStyle={{ color: '#a78bfa' }} />
                  <Radar dataKey="score" stroke="#8b5cf6" strokeWidth={2} fill="#7c3aed" fillOpacity={isCalculating ? 0.08 : 0.38} />
                </RadarChart>
              </ResponsiveContainer>
            </div>

            {sc && (
              <div className="mt-3 flex items-center gap-3 text-xs text-slate-500">
                <div className="flex items-center gap-1">
                  <Layers className="w-3.5 h-3.5 text-purple-500" />
                  <span>主要质量维度: <span className="text-purple-400 font-mono">{sc.quality_axis}</span></span>
                </div>
                {/* ★ v3: 展示分类方法 */}
                {methodBadge && (
                  <div className="flex items-center gap-1">
                    <span className="text-slate-600">|</span>
                    <span>引擎: <span className={`font-mono font-bold ${methodBadge.cls.split(' ')[0]}`}>{methodBadge.label}</span></span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Right: Log + Pricing */}
          <div className="flex flex-col gap-5">

            {/* Execution log */}
            <div className="bg-[#0a0f18] rounded-2xl border border-slate-800 p-5" style={{ minHeight: 160 }}>
              {/* ★ v3 标题 */}
              <h3 className="text-[10px] font-bold text-slate-600 uppercase tracking-widest mb-3 border-b border-slate-800/80 pb-2">
                Oracle Execution Log v3
              </h3>
              <div className="space-y-2">
                {EXEC_STEPS.map((step, i) => (
                  <div key={i} className={`font-mono text-xs transition-all duration-300 ${i === calcStep ? 'text-purple-400 animate-pulse font-bold' : i < calcStep ? 'text-slate-500' : 'opacity-0'}`}>
                    {i < calcStep ? '[DONE] ' : '>> '}{step}
                  </div>
                ))}
              </div>
            </div>

            {/* Pricing result card */}
            <div className={`flex-1 bg-gradient-to-br from-slate-900 to-purple-950/20 border border-purple-500/30 rounded-2xl p-6 relative overflow-hidden transition-all duration-700 ${isCalculating ? 'opacity-0 translate-y-4 blur-sm' : 'opacity-100 translate-y-0 blur-none'}`}>
              <ShieldCheck className="absolute -bottom-4 -right-4 w-40 h-40 rotate-12 text-purple-900/20 pointer-events-none" />

              <div className="flex justify-between items-start mb-4 pb-3 border-b border-purple-500/20">
                <div>
                  <h2 className="text-base font-bold text-white flex items-center">
                    <FileText className="w-4 h-4 mr-2 text-purple-400" /> 多模态统一定价凭证
                  </h2>
                  <p className="text-[10px] text-slate-500 font-mono mt-1 truncate">{valuationResult?.asset_hash}</p>
                </div>
              </div>

              {valuationResult?.status === 'rejected' ? (
                <div className="p-4 bg-red-900/20 border border-red-500/30 rounded-xl mb-4 text-red-400 font-bold text-center text-sm">
                  ⚠️ 熔断拦截: {valuationResult?.reason || '检测到极低质量/噪声数据，拒绝估值上链'}
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-2 gap-2.5 mb-4 relative z-10">
                    <div className="bg-slate-950/60 p-3 rounded-xl border border-slate-800">
                      <p className="text-[9px] text-slate-500 uppercase mb-1">六维综合质量</p>
                      <p className="text-lg font-mono text-white">{fv?.composite_quality}<span className="text-[10px] text-slate-500 ml-1">/100</span></p>
                    </div>
                    <div className="bg-slate-950/60 p-3 rounded-xl border border-slate-800">
                      <p className="text-[9px] text-slate-500 uppercase mb-1">模态 TEV</p>
                      <p className={`text-lg font-bold ${assetCategory === 'image' ? 'text-amber-400' : 'text-blue-400'}`}>{fv?.modality_tev}</p>
                    </div>
                    <div className="bg-slate-950/60 p-3 rounded-xl border border-slate-800">
                      <p className="text-[9px] text-slate-500 uppercase mb-1 flex items-center gap-1">
                        场景子权重 <Tag className="w-3 h-3" />
                      </p>
                      <p className={`text-lg font-bold ${sceneConfig?.color || 'text-purple-400'}`}>{fv?.scene_multiplier}</p>
                    </div>
                    <div className="bg-purple-900/20 p-3 rounded-xl border border-purple-500/30">
                      <p className="text-[9px] text-purple-400 uppercase mb-1">有效综合权重</p>
                      <p className="text-lg font-bold text-emerald-400">{fv?.effective_weight}</p>
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-2 mb-4 relative z-10">
                    <div className="bg-purple-900/20 p-3 rounded-xl border border-purple-500/30">
                      <p className="text-[9px] text-purple-400 uppercase mb-1">动态定价</p>
                      <p className="text-base font-mono text-white">{fv?.dynamic_price?.toLocaleString()}<span className="text-[9px] text-purple-400 ml-1">CRD</span></p>
                    </div>
                    <div className="bg-slate-950/60 p-3 rounded-xl border border-slate-800">
                      <p className="text-[9px] text-slate-500 uppercase mb-1">期权溢价</p>
                      <p className="text-base font-mono text-emerald-400">+{fv?.option_premium?.toLocaleString()}</p>
                    </div>
                    <div className="bg-slate-950/60 p-3 rounded-xl border border-slate-800">
                      <p className="text-[9px] text-slate-500 uppercase mb-1">创作者分成</p>
                      <p className="text-base font-bold text-amber-400">{fv?.creator_ratio}%</p>
                    </div>
                  </div>
                </>
              )}

              <button
                onClick={onNext}
                disabled={valuationResult?.status === 'rejected'}
                className={`w-full py-3.5 border rounded-xl font-bold transition-all flex items-center justify-center relative z-10 text-sm ${valuationResult?.status === 'rejected' ? 'bg-slate-800 text-slate-500 border-slate-700 cursor-not-allowed' : 'bg-purple-500/10 hover:bg-purple-500/20 border-purple-500/50 text-purple-300 shadow-[0_0_15px_rgba(139,92,246,0.1)] hover:shadow-[0_0_25px_rgba(139,92,246,0.25)]'}`}
              >
                下发至智能合约 AMM 交易大盘 <ArrowRight className="w-4 h-4 ml-2" />
              </button>
            </div>

          </div>
        </div>
      </div>
    </div>
  );
};

export default OracleValuationScreen;
