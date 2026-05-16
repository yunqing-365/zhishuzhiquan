import React, { useState, useEffect } from 'react';
import {
  Wallet, ShieldCheck, Database, Link as LinkIcon,
  Hexagon, Zap, CheckCircle, Activity, Server, TrendingUp, Mic,
} from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceDot, ReferenceLine,
} from 'recharts';

// ── 场景级 AMM 配置（与后端 scoring.py DOMAIN_DEMAND 同步）─────────────
// alpha: 联合曲线斜率，越大代表该场景需求对供给越敏感
// domainName: 面向 B 端展示的市场领域
// b2bCaller: 典型 B 端调用方
const SCENE_AMM_CONFIG = {
  // 文本场景
  medical_sft:  { alpha: 32, domainName: 'Medical SFT (罕见病垂直语料)',  b2bCaller: 'MedicalLLM-Corp',     icon: '🏥' },
  legal_doc:    { alpha: 28, domainName: 'Legal Doc (司法实务语料库)',    b2bCaller: 'LegalAI-Platform',    icon: '⚖️' },
  code_tech:    { alpha: 22, domainName: 'Code Tech (算法竞赛数据集)',    b2bCaller: 'CodeGen-Trainer',     icon: '💻' },
  creative:     { alpha: 18, domainName: 'Creative (RLHF写作偏好)',      b2bCaller: 'StoryGen-Inc',        icon: '✍️' },
  chat_qa:      { alpha: 15, domainName: 'Chat QA (对话指令微调)',        b2bCaller: 'ChatBot-Factory',     icon: '💬' },
  // 图像场景
  illustration: { alpha: 20, domainName: 'Visual Art (商业插画数据集)',   b2bCaller: 'Midjourney-LoRA',     icon: '🎨' },
  photo:        { alpha: 14, domainName: 'Photography (视觉基础训练集)', b2bCaller: 'CLIP-Trainer',        icon: '📷' },
  diagram:      { alpha: 10, domainName: 'Diagram (科学图表语料)',        b2bCaller: 'SciChart-AI',         icon: '📊' },
  screenshot:   { alpha:  6, domainName: 'Screenshot (UI训练集)',        b2bCaller: 'UIGen-Model',         icon: '🖥️' },
  // 音频场景
  general:      { alpha: 25, domainName: 'Audio General (多模态基础集)', b2bCaller: 'Whisper-Finetuner',  icon: '🎙️' },
};

// 音频细粒度场景覆盖（★ v4 对齐 scene_classifier.py AUDIO_SCENE_WEIGHTS，补全6类）
const AUDIO_SCENE_AMM = {
  speech_medical: { alpha: 38, domainName: 'Medical ASR (临床语音转录)',   b2bCaller: 'ClinicalASR-Corp',  icon: '🏥' },
  speech_legal:   { alpha: 32, domainName: 'Legal ASR (庭审语音结构化)',   b2bCaller: 'CourtBot-Platform', icon: '⚖️' },
  speech_edu:     { alpha: 20, domainName: 'Edu TTS (教育语音合成)',       b2bCaller: 'EduBot-Trainer',    icon: '📚' },
  music_original: { alpha: 22, domainName: 'Music Gen (音频生成训练集)',    b2bCaller: 'MusicLM-Studio',    icon: '🎵' },
  ambient_sfx:    { alpha: 14, domainName: 'SFX Pack (游戏音效数据集)',    b2bCaller: 'SoundGen-Engine',   icon: '🌿' },
  noise:          { alpha:  0, domainName: 'Noise (无效音频，拒绝入库)',   b2bCaller: 'N/A',               icon: '🚫' },
};

// 模态 paywall 拦截描述
const PAYWALL_LOG = {
  text:  (caller) => `>> [AI Paywall] ⚠️ 拦截成功！检测到 ${caller} 未授权 RAG 抓取 (Hash: 0xSimHash...)`,
  image: (caller) => `>> [AI Paywall] ⚠️ 拦截成功！检测到 ${caller} 未授权"融图"训练 (Hash: 0xDCT_pHash...)`,
  audio: (caller) => `>> [AI Paywall] ⚠️ 拦截成功！检测到 ${caller} 未授权音频转录抓取 (Hash: 0xAFP_acoustic...)`,
};

// ── 从 valuationResult 解析 AMM 参数 ──────────────────────────────────
// v4: amm_alpha 由后端 scoring.AMM_SCENE_CONFIG 计算后随 oracle response 下发
//     前端不再 hardcode alpha，直接读取，保证前后端 AMM 曲线完全一致
const resolveAmmConfig = (valuationResult, assetCategory) => {
  if (!valuationResult) {
    const defaults = { text: 'medical_sft', image: 'illustration', audio: 'speech_medical' };
    return SCENE_AMM_CONFIG[defaults[assetCategory]] || SCENE_AMM_CONFIG['general'];
  }

  const fv = valuationResult.final_valuation;
  const sc = valuationResult.scene_classification;

  // ★ v4: 优先使用后端下发的 amm_alpha（前后端统一参数源）
  if (fv?.amm_alpha != null) {
    // 从 oracle 拿到 alpha，再查本地 config 补充 domainName/b2bCaller/icon
    const baseConfig = (() => {
      if (assetCategory === 'audio' && sc?.audio_scene && AUDIO_SCENE_AMM[sc.audio_scene]) {
        return AUDIO_SCENE_AMM[sc.audio_scene];
      }
      return SCENE_AMM_CONFIG[sc?.scene] || SCENE_AMM_CONFIG['general'];
    })();
    // alpha 以后端为准，其余 label 信息从本地 config 取
    return { ...baseConfig, alpha: fv.amm_alpha };
  }

  // fallback: 后端未下发 amm_alpha 时用本地 config（兼容旧版本 API）
  if (assetCategory === 'audio' && sc?.audio_scene && AUDIO_SCENE_AMM[sc.audio_scene]) {
    return AUDIO_SCENE_AMM[sc.audio_scene];
  }
  return SCENE_AMM_CONFIG[sc?.scene] || SCENE_AMM_CONFIG['general'];
};

// ── SmartSplitScreen（v4：完全由 valuationResult 驱动，消灭 hardcode）─
const SmartSplitScreen = ({ valuationResult, assetCategory = 'text', onRestart }) => {
  // ── 从 oracle 结果提取真实定价参数 ────────────────────────────────
  const fv           = valuationResult?.final_valuation;
  const baseValue    = fv?.base_value    ?? (assetCategory === 'audio' ? 18600 : assetCategory === 'image' ? 9250 : 1200);
  const creatorRatio = fv?.creator_ratio ?? 85.0;
  const initDemand   = fv?.market_demand ?? 20;

  const ammConfig = resolveAmmConfig(valuationResult, assetCategory);
  const { alpha, domainName, b2bCaller, icon } = ammConfig;

  const [txStatus, setTxStatus]       = useState('idle');
  const [logs, setLogs]               = useState([]);
  const [demand, setDemand]           = useState(initDemand);
  const [currentPrice, setCurrentPrice] = useState(0);
  const [curveData, setCurveData]     = useState([]);

  const calculatePrice = (d) => Math.round(baseValue * (1000 + d * alpha) / 1000);

  useEffect(() => {
    const data = [];
    for (let i = 0; i <= 60; i += 2) data.push({ demand: i, price: calculatePrice(i) });
    setCurveData(data);
    setCurrentPrice(calculatePrice(demand));
  }, [demand, baseValue, alpha]);

  const addLog = (msg) => setLogs(prev => [...prev, msg]);

  const handleSimulatePayment = () => {
    setTxStatus('processing');
    setLogs([]);
    const paywallFn = PAYWALL_LOG[assetCategory] || PAYWALL_LOG['text'];
    setTimeout(() => addLog(`>> [B端大厂节点] ${b2bCaller} ${icon} 正在检索目标数据集...`), 500);
    setTimeout(() => addLog(paywallFn(b2bCaller)), 1500);
    setTimeout(() => addLog(`>> [智能合约] ${b2bCaller} 调用 purchaseAndCallData()，支付 Token 当量，AI-Echo 释放授权凭证...`), 2800);
    setTimeout(() => {
      const newDemand = demand + 1;
      const newPrice  = calculatePrice(newDemand);
      setDemand(newDemand);
      setCurrentPrice(newPrice);
      addLog(`>> [AMM 联合曲线] ${domainName} 领域热度上升，调用费自动上调至 ${newPrice.toLocaleString()} CRD 📈`);
    }, 4500);
    setTimeout(() => addLog(`>> [结算网关] 去中心化跨链分账 (创作者: ${creatorRatio}% | 平台: ${((100 - creatorRatio) * 0.6).toFixed(1)}% | 社区: ${((100 - creatorRatio) * 0.4).toFixed(1)}%)...`), 5500);
    setTimeout(() => {
      addLog('>> [SUCCESS] 创作者已获得本次大模型训练调用分润，合规流水上链完成 ✅');
      setTxStatus('success');
    }, 6500);
  };

  // 模态主题色
  const modalityColor = assetCategory === 'audio'
    ? 'text-emerald-400' : assetCategory === 'image'
    ? 'text-amber-400'   : 'text-blue-400';
  const modalityHexColor = assetCategory === 'audio'
    ? 'text-emerald-500' : assetCategory === 'image'
    ? 'text-amber-500'   : 'text-blue-500';

  return (
    <div className="min-h-screen relative flex items-center justify-center bg-[#050b14] overflow-hidden p-6 font-sans">
      <div className="absolute inset-0 bg-[linear-gradient(to_right,#1e293b_1px,transparent_1px),linear-gradient(to_bottom,#1e293b_1px,transparent_1px)] bg-[size:4rem_4rem] [mask-image:radial-gradient(ellipse_60%_60%_at_50%_50%,#000_70%,transparent_100%)] opacity-20" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[600px] bg-purple-900/10 rounded-full blur-[150px] pointer-events-none" />

      <div className="relative max-w-6xl w-full bg-slate-900/80 backdrop-blur-xl border border-slate-700/50 rounded-3xl p-8 md:p-10 shadow-2xl flex flex-col">

        {/* Header */}
        <div className="flex items-center justify-between mb-8 pb-4 border-b border-slate-700/50">
          <div className="flex flex-col">
            <div className="flex items-center space-x-4 mb-2">
              <div className="p-3 bg-purple-500/10 border border-purple-500/20 rounded-2xl">
                <LinkIcon className="w-8 h-8 text-purple-400" />
              </div>
              <h1 className="text-2xl font-extrabold tracking-wide text-white">SmartSplit · 去中心化语料交易大盘</h1>
            </div>
            <p className="text-slate-400 text-[11px] font-mono flex items-center bg-slate-950 px-3 py-1 rounded-full border border-slate-800 w-fit">
              <ShieldCheck className="w-3 h-3 mr-2 text-purple-500" />
              {icon} Domain: {domainName} | AMM Alpha: {alpha} | Bonding Curve v4
              {assetCategory === 'audio' && <><Mic className="w-3 h-3 ml-2 text-emerald-400" /><span className="ml-1 text-emerald-400">Audio Mode</span></>}
            </p>
          </div>
          <button onClick={onRestart} className="text-sm text-slate-400 hover:text-purple-400 transition-colors border border-slate-700 hover:border-purple-500/50 px-5 py-2 rounded-xl bg-slate-800/50 shadow-sm">
            重置沙箱 Demo
          </button>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 relative z-10">

          {/* 左侧控制台 */}
          <div className="lg:col-span-4 flex flex-col space-y-6">
            <div className="bg-slate-950/80 border border-slate-800 rounded-2xl p-6 relative overflow-hidden shadow-inner">
              <h3 className="text-sm font-bold text-slate-400 uppercase tracking-widest mb-4 flex items-center">
                <Server className="w-4 h-4 mr-2 text-indigo-400" /> 模拟大模型采买/拦截
              </h3>
              <div className="space-y-4 mb-6">
                {/* Oracle 来源信息 */}
                {valuationResult && (
                  <div className="p-2.5 bg-purple-950/30 rounded-lg border border-purple-500/20 text-[10px] font-mono text-purple-400 flex flex-col gap-0.5">
                    <span>🔮 Oracle 定价数据已接入</span>
                    <span className="text-slate-500 truncate">{valuationResult.asset_hash}</span>
                  </div>
                )}
                <div className="p-3 bg-slate-900 rounded-lg border border-slate-800">
                  <p className="text-[10px] text-slate-500 mb-1 uppercase">B端买方 / 调用方 (API Caller)</p>
                  <p className="text-sm text-white font-mono flex items-center">
                    <Hexagon className={`w-4 h-4 mr-2 ${modalityHexColor}`} />
                    {b2bCaller}
                  </p>
                </div>
                <div className="p-4 bg-slate-900 rounded-xl border border-purple-500/30 flex justify-between items-center bg-purple-950/20 shadow-[0_0_15px_rgba(168,85,247,0.1)]">
                  <div>
                    <p className="text-[10px] text-purple-400/80 uppercase">联合曲线实时报价</p>
                    <p className="text-xs text-slate-400 font-mono mt-1">Base: {baseValue.toLocaleString()} | α: {alpha} | D: {demand}</p>
                  </div>
                  <p className={`text-2xl font-bold font-mono transition-all duration-500 ${modalityColor}`}>
                    {currentPrice.toLocaleString()} <span className="text-xs opacity-50">CRD</span>
                  </p>
                </div>
              </div>
              <button
                onClick={handleSimulatePayment}
                disabled={txStatus === 'processing'}
                className={`w-full py-4 rounded-xl font-bold flex items-center justify-center space-x-2 transition-all duration-300 ${txStatus === 'idle' || txStatus === 'success' ? 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-[0_0_20px_rgba(79,70,229,0.4)]' : 'bg-slate-800 text-slate-400 cursor-wait'}`}
              >
                {(txStatus === 'idle' || txStatus === 'success') && <><Zap className="w-5 h-5" /><span>模拟 B端大厂合规采买</span></>}
                {txStatus === 'processing' && <><Activity className="w-5 h-5 animate-spin" /><span>AMM 撮合清算中...</span></>}
              </button>
            </div>

            {/* 执行日志 */}
            <div className="flex-1 bg-[#020617] border border-slate-800 rounded-2xl p-5 overflow-y-auto max-h-64 shadow-inner">
              <h3 className="text-[10px] font-bold text-slate-600 uppercase tracking-widest mb-3">Contract Execution Logs</h3>
              <div className="space-y-2">
                {logs.map((log, i) => (
                  <p key={i} className={`text-[11px] font-mono leading-relaxed ${log.includes('SUCCESS') ? 'text-emerald-400 font-bold' : log.includes('AMM') ? 'text-purple-400' : log.includes('⚠️') ? 'text-amber-400' : 'text-slate-400'}`}>
                    {log}
                  </p>
                ))}
                {txStatus === 'processing' && <p className="text-xs font-mono text-purple-500/50 animate-pulse">_</p>}
              </div>
            </div>
          </div>

          {/* 右侧可视化 */}
          <div className="lg:col-span-8 bg-slate-950/50 border border-slate-800 rounded-2xl p-6 relative flex flex-col justify-between shadow-lg">

            {/* AMM 联合曲线 */}
            <div className="mb-6">
              <h3 className="text-sm font-bold text-slate-300 flex items-center mb-4">
                <TrendingUp className="w-4 h-4 mr-2 text-purple-400" />
                资产流动性与联合曲线 (Bonding Curve · α={alpha})
              </h3>
              <div className="h-48 w-full bg-slate-900/50 rounded-xl p-2 border border-slate-800/50">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={curveData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                    <defs>
                      <linearGradient id="colorPrice" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%"  stopColor="#8b5cf6" stopOpacity={0.5} />
                        <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0}   />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                    <XAxis dataKey="demand" stroke="#475569" fontSize={10} tickLine={false} axisLine={false} />
                    <YAxis stroke="#475569" fontSize={10} tickLine={false} axisLine={false} />
                    <Tooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', borderRadius: '8px' }} itemStyle={{ color: '#a78bfa' }} />
                    <Area type="monotone" dataKey="price" stroke="#8b5cf6" strokeWidth={3} fillOpacity={1} fill="url(#colorPrice)" animationDuration={500} />
                    <ReferenceLine x={demand} stroke="#4f46e5" strokeDasharray="3 3" />
                    <ReferenceDot x={demand} y={currentPrice} r={6} fill="#8b5cf6" stroke="#fff" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* 分账清算流 */}
            <div className="relative mt-8">
              <div className={`mx-auto z-10 p-4 rounded-2xl border-2 flex flex-col items-center w-56 bg-slate-900 transition-all duration-500 ${txStatus === 'processing' ? 'border-purple-500 shadow-[0_0_20px_rgba(139,92,246,0.3)] scale-105' : 'border-slate-700'}`}>
                <Database className={`w-8 h-8 mb-2 ${txStatus === 'processing' ? 'text-purple-400 animate-pulse' : 'text-slate-500'}`} />
                <p className="text-[10px] text-slate-400 mb-1 uppercase">本次大厂采购金进入合约</p>
                <p className="text-2xl font-mono font-bold text-white transition-all">
                  {txStatus === 'idle' ? '0' : currentPrice.toLocaleString()}
                </p>
              </div>

              {/* 分账连线 */}
              <div className="h-16 w-full relative flex justify-center">
                <div className={`absolute top-0 w-[2px] h-8 transition-colors ${txStatus === 'success' ? 'bg-purple-500' : 'bg-slate-700'}`} />
                <div className={`absolute top-8 w-[70%] h-[2px] transition-colors ${txStatus === 'success' ? 'bg-purple-500' : 'bg-slate-700'}`} />
                <div className={`absolute top-8 left-[15%] w-[2px] h-8 transition-colors ${txStatus === 'success' ? 'bg-purple-500' : 'bg-slate-700'}`} />
                <div className={`absolute top-8 left-[50%]  w-[2px] h-8 transition-colors ${txStatus === 'success' ? 'bg-purple-500' : 'bg-slate-700'}`} />
                <div className={`absolute top-8 right-[15%] w-[2px] h-8 transition-colors ${txStatus === 'success' ? 'bg-purple-500' : 'bg-slate-700'}`} />
              </div>

              {/* 三方收益 */}
              <div className="w-full flex justify-between px-2">
                {[
                  { icon: Wallet,     label: `创作者分红 (${creatorRatio}%)`,                         ratio: creatorRatio / 100,            color: 'emerald' },
                  { icon: Activity,   label: `平台服务费 (${((100 - creatorRatio) * 0.6).toFixed(1)}%)`, ratio: (100 - creatorRatio) / 100 * 0.6, color: 'blue'    },
                  { icon: ShieldCheck,label: `社区基金 (${((100 - creatorRatio) * 0.4).toFixed(1)}%)`,   ratio: (100 - creatorRatio) / 100 * 0.4, color: 'amber'   },
                ].map(({ icon: Icon, label, ratio, color }, i) => (
                  <div key={i} className={`w-[32%] flex flex-col items-center p-4 rounded-xl border transition-all duration-500 ${i > 0 ? `delay-${i * 100}` : ''} ${txStatus === 'success' ? `bg-${color}-950/40 border-${color}-500/50 shadow-[0_0_15px_rgba(var(--tw-shadow-color),0.1)]` : 'bg-slate-900 border-slate-800'}`}>
                    <Icon className={`w-5 h-5 mb-2 ${txStatus === 'success' ? `text-${color}-400` : 'text-slate-600'}`} />
                    <p className="text-[10px] text-slate-400 text-center mb-1 uppercase">{label}</p>
                    <p className={`text-lg font-mono font-bold ${txStatus === 'success' ? `text-${color}-400` : 'text-slate-600'}`}>
                      {txStatus === 'success'
                        ? `+ ${(currentPrice * ratio).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                        : '0.00'}
                    </p>
                  </div>
                ))}
              </div>
            </div>

          </div>
        </div>
      </div>
    </div>
  );
};

export default SmartSplitScreen;
