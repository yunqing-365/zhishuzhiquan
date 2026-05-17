import React, { useState, useEffect } from 'react';
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer, Tooltip } from 'recharts';
import { Activity, ShieldCheck, FileText, Network, CheckCircle2, ArrowRight, Tag, Layers, FlaskConical, Mic } from 'lucide-react';

// 场景标签配置（与后端 scene_classifier.py + audio_adapter.py 完整同步）
const SCENE_LABELS = {
  medical_sft:  { label: '医疗 SFT',   color: 'text-red-400',     bg: 'bg-red-900/20 border-red-500/30',       mult: '1.35×' },
  legal_doc:    { label: '法律文书',   color: 'text-orange-400',  bg: 'bg-orange-900/20 border-orange-500/30', mult: '1.20×' },
  code_tech:    { label: '代码技术',   color: 'text-cyan-400',    bg: 'bg-cyan-900/20 border-cyan-500/30',     mult: '1.10×' },
  creative:     { label: '创意写作',   color: 'text-pink-400',    bg: 'bg-pink-900/20 border-pink-500/30',     mult: '0.90×' },
  chat_qa:      { label: '问答对话',   color: 'text-blue-400',    bg: 'bg-blue-900/20 border-blue-500/30',     mult: '0.80×' },
  illustration: { label: '原创插画',   color: 'text-amber-400',   bg: 'bg-amber-900/20 border-amber-500/30',   mult: '1.50×' },
  photo:        { label: '摄影作品',   color: 'text-yellow-400',  bg: 'bg-yellow-900/20 border-yellow-500/30', mult: '1.00×' },
  screenshot:   { label: '截图素材',   color: 'text-slate-400',   bg: 'bg-slate-900/30 border-slate-600/30',   mult: '0.25×' },
  diagram:      { label: '图表图解',   color: 'text-violet-400',  bg: 'bg-violet-900/20 border-violet-500/30', mult: '0.55×' },
  noise:        { label: '噪声/废话',  color: 'text-red-500',     bg: 'bg-red-950/30 border-red-700/30',       mult: '0.05×' },
  general:      { label: '通用音频',   color: 'text-slate-400',   bg: 'bg-slate-900/30 border-slate-600/30',   mult: '1.00×' },
};

// 音频细粒度场景标签（audio_scene 字段，仅音频模态）
const AUDIO_SCENE_LABELS = {
  speech_medical: { label: '🏥 医疗语音', color: 'text-red-300' },
  speech_legal:   { label: '⚖️ 法律音频',  color: 'text-orange-300' },
  speech_edu:     { label: '📚 教育语音',  color: 'text-cyan-300' },
  music_original: { label: '🎵 原创音乐',  color: 'text-pink-300' },
  ambient_sfx:    { label: '🌿 环境音效',  color: 'text-slate-300' },
  noise:          { label: '🚫 噪声',      color: 'text-red-400' },
};

// 分类方法徽章（★ v4 新增: acoustic / fusion / text_proxy）
const METHOD_BADGE = {
  rule:        { label: 'rule',        cls: 'text-slate-400  border-slate-600/40  bg-slate-900/40'  },
  ml:          { label: 'ML',          cls: 'text-cyan-400   border-cyan-500/40   bg-cyan-900/20'   },
  hybrid:      { label: 'hybrid',      cls: 'text-purple-400 border-purple-500/40 bg-purple-900/20' },
  override:    { label: 'override',    cls: 'text-amber-400  border-amber-500/40  bg-amber-900/20'  },
  // 音频双通道（scene_classifier v4）
  acoustic:    { label: '🎙 acoustic',   cls: 'text-emerald-400 border-emerald-500/40 bg-emerald-900/20' },
  fusion:      { label: '⚡ fusion',     cls: 'text-teal-400   border-teal-500/40   bg-teal-900/20'    },
  text_proxy:  { label: '📝 text_proxy', cls: 'text-slate-400  border-slate-600/40  bg-slate-900/40'  },
};

// 模态 adapter 标签
const ADAPTER_LABEL = {
  text:  { label: 'Text-Adapter',  cls: 'bg-blue-900/20 text-blue-400 border-blue-500/30'    },
  image: { label: 'Image-Adapter', cls: 'bg-amber-900/20 text-amber-400 border-amber-500/30' },
  audio: { label: 'Audio-Adapter', cls: 'bg-emerald-900/20 text-emerald-400 border-emerald-500/30' },
};

// Mock 数据（API 未启动时）
// ── 音频细粒度场景 mock 参数表（与 scene_classifier.py AUDIO_SCENE_WEIGHTS 对齐）──
// amm_alpha / market_demand 与后端 scoring.py AMM_SCENE_CONFIG 同步
const AUDIO_SCENE_MOCK = {
  speech_medical: { alpha: 38, demand: 28, baseVal: 22400, dynPrice: 27430, optPremium: 7250,
                    method: 'fusion',     scores: [87, 90, 84, 95, 88, 91] },
  speech_legal:   { alpha: 32, demand: 22, baseVal: 18900, dynPrice: 23100, optPremium: 5800,
                    method: 'fusion',     scores: [84, 88, 82, 90, 85, 89] },
  speech_edu:     { alpha: 20, demand: 15, baseVal: 10200, dynPrice: 12400, optPremium: 2900,
                    method: 'text_proxy', scores: [80, 85, 78, 82, 83, 84] },
  music_original: { alpha: 22, demand: 18, baseVal: 14300, dynPrice: 17500, optPremium: 4200,
                    method: 'acoustic',   scores: [85, 82, 91, 88, 78, 86] },
  ambient_sfx:    { alpha: 14, demand: 10, baseVal:  6800, dynPrice:  8200, optPremium: 1600,
                    method: 'acoustic',   scores: [78, 76, 80, 72, 70, 75] },
  noise:          { alpha:  0, demand:  0, baseVal:   120, dynPrice:   140, optPremium:   20,
                    method: 'acoustic',   scores: [20, 18, 22, 10, 12, 15] },
};

const buildMock = (assetCategory, sceneOverride) => {
  const isImg   = assetCategory === 'image';
  const isAudio = assetCategory === 'audio';

  // 音频模态：从 AUDIO_SCENE_MOCK 查表，支持 sceneOverride 切换场景
  if (isAudio) {
    // sceneOverride 传入的是 TEV 场景（如 medical_sft），需反向找 audio_scene
    // 默认展示 speech_medical
    const TEV_TO_AUDIO = {
      medical_sft: 'speech_medical',
      legal_doc:   'speech_legal',
      chat_qa:     'speech_edu',
      creative:    'music_original',
      general:     'ambient_sfx',
      noise:       'noise',
    };
    const audioScene = (sceneOverride && TEV_TO_AUDIO[sceneOverride])
      ? TEV_TO_AUDIO[sceneOverride]
      : 'speech_medical';
    const p = AUDIO_SCENE_MOCK[audioScene] || AUDIO_SCENE_MOCK.speech_medical;
    const tevScene = sceneOverride || 'medical_sft';
    // scene_multiplier 从 AUDIO_SCENE_WEIGHTS 对应的 TEXT_SCENE_WEIGHTS 取
    const scMultMap = {
      speech_medical: '1.35x', speech_legal: '1.20x', speech_edu: '0.80x',
      music_original: '0.90x', ambient_sfx:  '1.00x', noise:      '0.05x',
    };
    return {
      status: 'success',
      asset_hash: '0xAFP_mock_' + Math.abs(Array.from(sceneOverride||'speech_medical').reduce((h,c)=>Math.imul(31,h)+c.charCodeAt(0)|0,0)).toString(16).toUpperCase().padStart(8,'0') + '_DEMO',
      scene_classification: {
        scene: tevScene,
        confidence: sceneOverride ? 1.0 : 0.87,
        quality_axis: 'snr',
        method: sceneOverride ? 'override' : p.method,  // ★ v4: 真实 method 类型
        audio_scene: audioScene,                         // ★ v4: 细粒度标签
      },
      metrics: [
        '频谱熵(信息密度)', 'PESQ感知信噪比', '语音指令连贯性',
        '音频库稀缺度', 'ASR微调增益', 'KNN-Shapley贡献度',
      ].map((n, i) => ({ subject: n, score: p.scores[i], fullMark: 100 })),
      final_valuation: {
        composite_quality: Math.round(p.scores.reduce((a,b)=>a+b,0)/p.scores.length * 10) / 10,
        modality_tev: '120x',
        scene_multiplier: scMultMap[audioScene] || '1.00x',
        effective_weight: '162x',
        base_value:    p.baseVal,
        dynamic_price: p.dynPrice,
        option_premium: p.optPremium,
        sigma: 0.64,
        market_demand: p.demand,
        amm_alpha:     p.alpha,   // ★ v4: 6种场景各有不同斜率
        creator_ratio: 87.5,
      },
      meta: {
        modality: 'audio',
        modality_label: '音频语音',
        adapter_version: 'v1',
        shapley_confidence: 0.82,
      },
    };
  }

  // 文本 / 图像模态（保持原逻辑）
  const mockScene = sceneOverride || (isImg ? 'illustration' : 'medical_sft');
  const tev   = isImg ? '50x'    : '1x';
  const scMult= isImg ? '1.5x'   : '1.35x';
  const effW  = isImg ? '75x'    : '1.35x';
  const baseVal   = isImg ? 13275 : 239;
  const dynPrice  = isImg ? 16279 : 298;
  const optPremium= isImg ? 4012  : 68;
  const metricNames = {
    text:  ['信息熵密度(抗废话)', '场景信噪比', '实体拓扑密度(GraphRAG)', '语料库稀缺度', '大模型微调增益', 'KNN-Shapley贡献度'],
    image: ['CLIP语义对齐度', '频域隐写鲁棒性(DWT)', 'LAION美学评级', '画派风格稀缺度', 'LoRA微调增益', 'KNN-Shapley贡献度'],
  }[assetCategory] || [];
  const scores = isImg ? [92, 95, 96, 96, 89, 85] : [88, 82, 85, 93, 80, 91];
  return {
    status: 'success',
    asset_hash: (() => {
      const seed = Array.from((assetCategory + (sceneOverride||mockScene)).slice(0,12))
        .reduce((h,c) => Math.imul(31,h)+c.charCodeAt(0)|0, 0);
      const hex = Math.abs(seed).toString(16).toUpperCase().padStart(8,'0');
      return isImg ? '0xPH_mock_' + hex + '_DEMO' : '0xSH_mock_' + hex + '_DEMO';
    })(),
    scene_classification: {
      scene: mockScene, confidence: sceneOverride ? 1.0 : 0.87,
      quality_axis: isImg ? 'structure' : 'snr',
      method: sceneOverride ? 'override' : 'rule',
      audio_scene: null,
    },
    metrics: metricNames.map((n, i) => ({ subject: n, score: scores[i], fullMark: 100 })),
    final_valuation: {
      composite_quality: 88.5, modality_tev: tev,
      scene_multiplier: scMult, effective_weight: effW,
      base_value: baseVal, dynamic_price: dynPrice,
      option_premium: optPremium, sigma: 0.64,
      market_demand: isImg ? 22 : 28,
      amm_alpha:     isImg ? 20 : 32,
      creator_ratio: 87.5,
    },
    meta: {
      modality: assetCategory,
      modality_label: isImg ? '图像画作' : '文本语料',
      adapter_version: 'v2',
      shapley_confidence: 0.85,
    },
  };
};

// ★ v4: 接收 audioData prop，onNext 回传完整 valuationResult
const OracleValuationScreen = ({ assetData, assetCategory, isZkMode, sceneOverride, audioData, onNext }) => {
  const [isCalculating, setIsCalculating]     = useState(true);
  const [calcStep, setCalcStep]               = useState(0);
  const [chartData, setChartData]             = useState([]);
  const [valuationResult, setValuationResult] = useState(null);
  // ★ v5: 数据来源状态 — 'api' | 'mock' | 'error'
  const [dataSource, setDataSource]           = useState(null);
  const [apiError, setApiError]               = useState(null);

  const EXEC_STEPS = [
    `[Stage 1] 模态路由 → [${(ADAPTER_LABEL[assetCategory]?.label || 'Adapter').toUpperCase()}] 初始化，向量知识库接入...`,
    sceneOverride
      ? `[Stage 2] 场景覆盖已激活 → 跳过 SceneClassifier，强制场景: ${sceneOverride}`
      : `[Stage 2] SceneClassifier v4 dual-channel → 识别 ${assetCategory === 'audio' ? '音频场景 (声学×0.65 + 文本KWS×0.35)' : '文本/图像场景子类型 (rule+ML hybrid)'}...`,
    `[Stage 3] 场景自适应特征提取 → ${assetCategory === 'audio' ? 'MFCC嵌入 + PESQ代理SNR + 频谱熵' : assetCategory === 'image' ? 'pHash + LAION美学 + DWT' : 'Shannon熵 + GraphRAG + SimHash'}...`,
    `[Stage 4] TEV 双层乘数 → 模态基础倍率 × 场景权重 → 复合评分...`,
    '[Stage 5] AMM 联合曲线 + KNN-Shapley + 实物期权 → 统一定价上链...',
  ];

  useEffect(() => {
    const defaultNames = {
      text:  ['信息熵密度(抗废话)', '场景信噪比', '实体拓扑密度(GraphRAG)', '语料库稀缺度', '大模型微调增益', 'KNN-Shapley贡献度'],
      image: ['CLIP语义对齐度', '频域隐写鲁棒性(DWT)', 'LAION美学评级', '画派风格稀缺度', 'LoRA微调增益', 'KNN-Shapley贡献度'],
      audio: ['频谱熵(信息密度)', 'PESQ感知信噪比', '语音指令连贯性', '音频库稀缺度', 'ASR微调增益', 'KNN-Shapley贡献度'],
    }[assetCategory] || [];
    setChartData(defaultNames.map(n => ({ subject: n, score: 0, fullMark: 100 })));

    let cur = 0;
    const ticker = setInterval(() => {
      if (cur < EXEC_STEPS.length) { setCalcStep(cur); cur++; }
    }, 750);

    // ★ v5: 归一化后端 metrics 格式
    const normalizeMetrics = (raw) => {
      if (!raw || !Array.isArray(raw)) return null;
      // 后端格式: [{subject, score, fullMark}] 或 [{name, value}]
      return raw.map(m => ({
        subject:  m.subject ?? m.name ?? m.label ?? '维度',
        score:    Number(m.score  ?? m.value ?? 0),
        fullMark: m.fullMark ?? 100,
      }));
    };

    const fetchValuation = async () => {
      // ★ v5: 5 秒超时，避免后端未启动时 hang 住
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 5000);
      try {
        const body = {
          asset_category: assetCategory,
          description:    assetData,
          is_zk_mode:     isZkMode,
          scene_override: sceneOverride ?? null,
          audio_data:     audioData ?? null,
        };
        const res = await fetch('http://127.0.0.1:8000/api/valuate', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify(body),
          signal:  controller.signal,
        });
        clearTimeout(timer);
        if (!res.ok) {
          const errText = await res.text().catch(() => 'HTTP Error');
          throw new Error(`HTTP ${res.status}: ${errText.slice(0, 120)}`);
        }
        const data = await res.json();
        const metrics = normalizeMetrics(data.metrics);
        setTimeout(() => {
          clearInterval(ticker);
          setDataSource('api');          // ★ v5: 标记真实 API
          setValuationResult(data);
          if (metrics) setChartData(metrics);
          setIsCalculating(false);
        }, 4200);
      } catch (err) {
        clearTimeout(timer);
        // ★ v5: 区分超时 vs 网络错误 vs 服务器错误
        const isTimeout = err.name === 'AbortError';
        const errMsg = isTimeout ? '后端连接超时 (5s)' : err.message;
        const mock = buildMock(assetCategory, sceneOverride);
        setTimeout(() => {
          clearInterval(ticker);
          setDataSource('mock');         // ★ v5: 标记 mock 来源
          setApiError(errMsg);           // ★ v5: 保存错误信息
          setValuationResult(mock);
          setChartData(mock.metrics);
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
  const meta        = valuationResult?.meta;
  const methodBadge = METHOD_BADGE[sc?.method] ?? null;
  const audioScene  = AUDIO_SCENE_LABELS[sc?.audio_scene] ?? null;
  const adapterCls  = ADAPTER_LABEL[assetCategory] ?? ADAPTER_LABEL['text'];

  // ★ v4: onNext 携带完整 valuationResult
  const handleNext = () => onNext(valuationResult);

  return (
    <div className="min-h-screen relative flex items-center justify-center bg-slate-950 p-6 font-sans">
      <div className="absolute top-0 left-0 w-[600px] h-[600px] bg-purple-900/10 rounded-full blur-[200px] pointer-events-none" />
      <div className="relative max-w-6xl w-full bg-slate-900/80 backdrop-blur-2xl border border-slate-700/50 rounded-3xl p-8 shadow-2xl flex flex-col" style={{ minHeight: '88vh' }}>

        {/* Header */}
        <div className="flex items-center justify-between mb-6 pb-4 border-b border-slate-700/50">
          <div className="flex flex-col gap-2">
            <div className="flex items-center space-x-3">
              <Network className="w-7 h-7 text-purple-400" />
              <h1 className="text-xl font-extrabold tracking-wide text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-indigo-300">
                多模态统一定价预言机 v4
              </h1>
              {sceneOverride && (
                <span className="flex items-center gap-1 px-2 py-0.5 rounded border border-amber-500/40 bg-amber-900/20 text-amber-300 text-[10px] font-mono">
                  <FlaskConical className="w-3 h-3" /> 调试·覆盖: {sceneOverride}
                </span>
              )}
            </div>

            {/* Pipeline breadcrumb */}
            <div className="flex items-center flex-wrap gap-1 text-[10px] font-mono tracking-wider">
              <span className={`px-2.5 py-1 rounded border ${adapterCls.cls}`}>
                {assetCategory === 'audio' && <Mic className="w-3 h-3 inline mr-1" />}
                {adapterCls.label}
                {meta?.adapter_version && <span className="ml-1 opacity-50">{meta.adapter_version}</span>}
              </span>
              <ArrowRight className="w-3 h-3 text-slate-600" />
              {sc ? (
                <span className={`px-2.5 py-1 rounded border flex items-center gap-1 ${sceneConfig.bg} ${sceneConfig.color}`}>
                  <Tag className="w-3 h-3" /> {sceneConfig.label}
                  <span className="opacity-60 ml-1">{Math.round(sc.confidence * 100)}%</span>
                  {methodBadge && (
                    <span className={`ml-1 px-1.5 py-0.5 rounded border text-[9px] font-bold ${methodBadge.cls}`}>
                      {methodBadge.label}
                    </span>
                  )}
                  {/* ★ v4: 音频细粒度标签 */}
                  {audioScene && (
                    <span className={`ml-1 px-1.5 py-0.5 rounded bg-slate-900/60 border border-slate-700 text-[9px] font-bold ${audioScene.color}`}>
                      {audioScene.label}
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

          <div className="flex items-center gap-2">
            {/* ★ v5: 数据来源徽章 */}
            {dataSource === 'api' && (
              <span className="px-2.5 py-1 rounded-full border border-emerald-500/40 bg-emerald-900/20 text-emerald-400 text-[10px] font-mono font-bold">
                ● 真实 API
              </span>
            )}
            {dataSource === 'mock' && (
              <span className="px-2.5 py-1 rounded-full border border-amber-500/40 bg-amber-900/20 text-amber-400 text-[10px] font-mono font-bold" title={apiError || ''}>
                ◎ Mock 降级
              </span>
            )}
            <div className={`px-4 py-2 rounded-full border text-sm font-bold flex items-center ${isCalculating ? 'bg-amber-500/10 border-amber-500/30 text-amber-400' : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'}`}>
              {isCalculating ? <Activity className="w-4 h-4 mr-2 animate-spin" /> : <CheckCircle2 className="w-4 h-4 mr-2" />}
              {isCalculating ? 'Computing...' : 'Valuation Complete'}
            </div>
          </div>
        </div>

        {/* ★ v5: Mock 降级提示条 */}
        {dataSource === 'mock' && (
          <div className="mb-4 px-4 py-2 rounded-xl border border-amber-500/30 bg-amber-950/30 flex items-center gap-2 text-[11px] font-mono text-amber-400">
            <span className="shrink-0">⚠ Mock 降级模式</span>
            <span className="text-amber-600 truncate">后端未响应 — {apiError || '连接失败'} — 以下数据为本地仿真，不代表真实估值</span>
            <span className="ml-auto shrink-0 text-amber-600">启动后端: cd ai-echo-backend && uvicorn oracle_engine:app</span>
          </div>
        )}
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
                  <Radar
                    dataKey="score" stroke="#8b5cf6" strokeWidth={2}
                    fill={assetCategory === 'audio' ? '#10b981' : assetCategory === 'image' ? '#f59e0b' : '#7c3aed'}
                    fillOpacity={isCalculating ? 0.08 : 0.38}
                  />
                </RadarChart>
              </ResponsiveContainer>
            </div>
            {sc && (
              <div className="mt-3 flex items-center gap-3 text-xs text-slate-500 flex-wrap">
                <div className="flex items-center gap-1">
                  <Layers className="w-3.5 h-3.5 text-purple-500" />
                  <span>主要质量维度: <span className="text-purple-400 font-mono">{sc.quality_axis}</span></span>
                </div>
                {methodBadge && (
                  <div className="flex items-center gap-1">
                    <span className="text-slate-600">|</span>
                    <span>分类引擎: <span className={`font-mono font-bold ${methodBadge.cls.split(' ')[0]}`}>{methodBadge.label}</span></span>
                  </div>
                )}
                {meta?.modality_label && (
                  <div className="flex items-center gap-1">
                    <span className="text-slate-600">|</span>
                    <span className="text-slate-400">{meta.modality_label}</span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Right: Log + Pricing */}
          <div className="flex flex-col gap-5">

            {/* Execution log */}
            <div className="bg-[#0a0f18] rounded-2xl border border-slate-800 p-5" style={{ minHeight: 160 }}>
              <h3 className="text-[10px] font-bold text-slate-600 uppercase tracking-widest mb-3 border-b border-slate-800/80 pb-2">
                Oracle Execution Log v4
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
                      <p className={`text-lg font-bold ${assetCategory === 'audio' ? 'text-emerald-400' : assetCategory === 'image' ? 'text-amber-400' : 'text-blue-400'}`}>{fv?.modality_tev}</p>
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
                onClick={handleNext}
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
