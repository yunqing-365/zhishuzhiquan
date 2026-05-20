import React, { useState, useEffect, useCallback } from 'react';
import {
  Wallet, ShieldCheck, Database, Link as LinkIcon, ExternalLink,
  Hexagon, Zap, CheckCircle, Activity, Server, TrendingUp, Mic, AlertTriangle,
} from 'lucide-react';
import WalletButton               from './web3/WalletButton';
import { useAIEchoContract }      from './web3/useAIEchoContract';
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

// ★ v5: Paywall 拦截日志生成器 — 接入真实 asset_hash 和 hashAlgo
// 格式与合约 PaywallTriggered 事件的 fingerprintType 字段完全对齐
const buildPaywallLog = (caller, modality, assetHash) => {
  // 根据模态选取合约对应的指纹算法标记（与 HashAlgorithm enum 对齐）
  const fpMeta = {
    text:  { algo: 'SimHash-64bit',    prefix: '0xSH_',  contract: 'HashAlgorithm.SIMHASH' },
    image: { algo: 'DCT-pHash-64bit',  prefix: '0xPH_',  contract: 'HashAlgorithm.PHASH'   },
    audio: { algo: 'AFP-SHA256-48bit', prefix: '0xAFP_', contract: 'HashAlgorithm.AFP'      },
    video: { algo: 'VID-stub-64bit',   prefix: '0xVID_', contract: 'HashAlgorithm.VIDHASH'  },
  };
  const { algo, prefix, contract } = fpMeta[modality] || fpMeta.text;
  // 从 asset_hash 截取后8位作为指纹摘要展示，与合约 _fingerprintTypeStr() 输出对齐
  const hashSnippet = assetHash
    ? prefix + String(assetHash).slice(-8).toUpperCase().padStart(8, '0')
    : prefix + 'XXXXXXXX';
  return `>> [AI Paywall · verifyAccess()] ⚠️ 拦截成功！检测到 ${caller} 未授权访问\n   ↳ 资产指纹: ${hashSnippet}  算法: ${algo}\n   ↳ 合约校验: ${contract}  结果: ACCESS_DENIED — 无有效 AccessToken\n   ↳ 触发事件: PaywallTriggered(assetHash, unauthorizedCaller, "${modality}", "${algo}")`;
};

// ── 从 valuationResult 解析 AMM 参数 ──────────────────────────────────
// v4: amm_alpha 由后端 scoring.AMM_SCENE_CONFIG 计算后随 oracle response 下发
//     前端不再 hardcode alpha，直接读取，保证前后端 AMM 曲线完全一致
const resolveAmmConfig = (valuationResult, assetCategory) => {
  if (!valuationResult) {
    const defaults = { text: 'medical_sft', image: 'illustration', audio: 'speech_medical', video: 'illustration' };
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
const SmartSplitScreen = ({ valuationResult, assetCategory = 'text', onRestart, onBack }) => {
  // ── 从 oracle 结果提取真实定价参数 ────────────────────────────────
  const fv           = valuationResult?.final_valuation;
  const baseValue    = fv?.base_value    ?? (assetCategory === 'audio' ? 18600 : assetCategory === 'image' ? 9250 : assetCategory === 'video' ? 96400 : 1200);
  const creatorRatio = fv?.creator_ratio ?? 85.0;
  const initDemand   = fv?.market_demand ?? 20;

  const ammConfig = resolveAmmConfig(valuationResult, assetCategory);
  const { alpha, domainName, b2bCaller, icon } = ammConfig;

  const [txStatus, setTxStatus]       = useState('idle');
  const [logs, setLogs]               = useState([]);
  const [demand, setDemand]           = useState(initDemand);
  const [currentPrice, setCurrentPrice] = useState(0);
  const [curveData, setCurveData]     = useState([]);
  const [purchaseHistory, setPurchaseHistory] = useState([]); // ★ v6: 历史采购点

  // ── Web3 合约 Hook ─────────────────────────────────────────────
  const contract = useAIEchoContract();
  // 从 valuationResult 提取注册所需参数
  const sc = valuationResult?.scene_classification;
  const assetHashStr = valuationResult?.asset_hash ?? null;
  // ★ ZK 承诺（阶段 2）
  const zkProof      = valuationResult?.zk_proof ?? null;
  const zkCommitment = zkProof?.commitment ?? null;

  const calculatePrice = (d) => Math.round(baseValue * (1000 + d * alpha) / 1000);

  useEffect(() => {
    const data = [];
    for (let i = 0; i <= 60; i += 2) data.push({ demand: i, price: calculatePrice(i) });
    setCurveData(data);
    setCurrentPrice(calculatePrice(demand));
  }, [demand, baseValue, alpha]);

  const addLog = (msg) => setLogs(prev => [...prev, msg]);

  // ── 注册资产上链（真实合约，钱包已连接时执行）──────────────────
  const handleRegisterOnChain = useCallback(async () => {
    setTxStatus('processing');
    setLogs([]);
    addLog('>> [Web3] 检测到钱包已连接，启动真实链上交互模式...');
    addLog(`>> [Web3] 目标网络: ${contract.targetChain?.name} (chain ${contract.targetChain?.id})`);
    try {
      addLog('>> [步骤 1/3] 调用 registerAsset() — 资产上链注册...');
      addLog(`>> [ZK] 承诺凭证: ${zkCommitment ? zkCommitment.slice(0, 18) + '…' : '无（zkML 未启用）'}`);
      const regTxHash = await contract.registerAsset({
        assetHashStr:  assetHashStr,
        modality:      assetCategory,
        domainKey:     sc?.scene || 'general',
        audioScene:    sc?.audio_scene || '',
        baseValue:     fv?.base_value || 0,
        zkCommitment:  zkCommitment,
      });
      addLog(`>> [链上确认] registerAsset 交易广播: ${regTxHash?.slice(0, 18)}...`);
      addLog('>> [步骤 2/3] 等待区块确认...');
      // 等待 contract hook 的 isConfirmed
      await new Promise(res => setTimeout(res, 3000));
      addLog('>> [步骤 3/3] 链上指纹注册完成，AssetRegistered 事件已发出 ✅');
      addLog(`>> [TxHash] ${regTxHash}`);
      const newDemand = demand + 1;
      setDemand(newDemand);
      setCurrentPrice(calculatePrice(newDemand));
      addLog('>> [SUCCESS] 资产已成功注册至 AI-Echo Protocol 合约 ✅');
      setTxStatus('success');
    } catch (e) {
      addLog(`>> [ERROR] 链上交易失败: ${e?.shortMessage || e?.message}`);
      setTxStatus('error');
    }
  }, [contract, assetHashStr, assetCategory, sc, fv, demand]);

  // ── Mock 演示（无钱包时保留原有体验）──────────────────────────────
  const handleSimulateMock = useCallback(() => {
    setTxStatus('processing');
    setLogs([]);
    const assetHash = assetHashStr;
    const paywallLog = buildPaywallLog(b2bCaller, assetCategory, assetHash);
    const hashSnippet = assetHash ? String(assetHash).slice(-8).toUpperCase().padStart(8, '0') : '????????';
    setTimeout(() => addLog(`>> [B端大厂节点] ${b2bCaller} ${icon} 正在检索目标数据集...`), 500);
    setTimeout(() => addLog(paywallLog), 1500);
    setTimeout(() => addLog(`>> [链上合约·仿真] purchaseAndCallData(hash=0x${hashSnippet}, quota=100, ttl=30d)
   ↳ AMM 实时报价验证通过，颁发 AccessToken...`), 2800);
    setTimeout(() => {
      const newDemand = demand + 1;
      setDemand(newDemand);
      const newPrice = calculatePrice(newDemand);
      setCurrentPrice(newPrice);
      setPurchaseHistory(h => [...h, { demand: newDemand, price: newPrice, label: 'SIM' }]);
      addLog(`>> [AMM 联合曲线] ${domainName} 热度上升，报价调整至 ${newPrice.toLocaleString()} CRD 📈`);
    }, 4500);
    setTimeout(() => addLog(`>> [结算网关] 创作者: ${creatorRatio}% | 平台: ${((100 - creatorRatio) * 0.6).toFixed(1)}% | 社区: ${((100 - creatorRatio) * 0.4).toFixed(1)}%
   ↳ PaymentSettled 已上链（仿真）`), 5500);
    setTimeout(() => {
      addLog('>> [SUCCESS] 创作者已获得分润，合规流水上链完成 ✅（仿真模式）');
      setTxStatus('success');
    }, 6500);
  }, [b2bCaller, icon, assetCategory, assetHashStr, demand, domainName, creatorRatio]);

  // ── 统一入口：有钱包用真实合约，否则 Mock ─────────────────────────
  const handleSimulatePayment = useCallback(() => {
    if (contract.contractReady) {
      handleRegisterOnChain();
    } else {
      handleSimulateMock();
    }
  }, [contract.contractReady, handleRegisterOnChain, handleSimulateMock]);

  // 模态主题色
  const modalityColor = assetCategory === 'audio'
    ? 'text-emerald-400' : assetCategory === 'image'
    ? 'text-amber-400'   : assetCategory === 'video'
    ? 'text-violet-400'  : 'text-blue-400';
  const modalityHexColor = assetCategory === 'audio'
    ? 'text-emerald-500' : assetCategory === 'image'
    ? 'text-amber-500'   : assetCategory === 'video'
    ? 'text-violet-500'  : 'text-blue-500';

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
          <div className="flex items-center gap-2">
            {onBack && (
              <button onClick={onBack} className="text-sm text-slate-500 hover:text-slate-300 transition-colors border border-slate-800 hover:border-slate-600 px-4 py-2 rounded-xl bg-slate-900/50 shadow-sm">
                ← 返回估值
              </button>
            )}
            <button onClick={onRestart} className="text-sm text-slate-400 hover:text-purple-400 transition-colors border border-slate-700 hover:border-purple-500/50 px-5 py-2 rounded-xl bg-slate-800/50 shadow-sm">
              重置沙箱 Demo
            </button>
          </div>
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
                    {zkCommitment && (
                      <span className="text-purple-500/80 truncate flex items-center gap-1">
                        <span className="text-purple-600">zk·</span>
                        {zkCommitment.slice(0,18)}…{zkCommitment.slice(-6)}
                        <span className="text-slate-600 ml-1">{zkProof?.proof_type}</span>
                      </span>
                    )}
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
              {/* ── 钱包状态栏 ────────────────────────────────────── */}
              <div className="flex items-center justify-between mb-2">
                <WalletButton size="sm" showChain={true} />
                {contract.isConnected && !contract.isCorrectChain && (
                  <span className="text-[10px] text-amber-400 font-mono flex items-center gap-1">
                    <AlertTriangle className="w-3 h-3" /> 请切换到 {contract.targetChain?.name}
                  </span>
                )}
                {contract.contractReady && (
                  <span className="text-[10px] text-emerald-400 font-mono flex items-center gap-1">
                    <CheckCircle className="w-3 h-3" /> 真实链上模式
                  </span>
                )}
                {!contract.isConnected && (
                  <span className="text-[10px] text-slate-500 font-mono">未连接 — 仿真演示模式</span>
                )}
              </div>

              <button
                onClick={handleSimulatePayment}
                disabled={txStatus === 'processing' || (contract.isConnected && !contract.isCorrectChain)}
                className={`w-full py-4 rounded-xl font-bold flex items-center justify-center space-x-2 transition-all duration-300 ${
                  txStatus === 'idle' || txStatus === 'success' || txStatus === 'error'
                    ? contract.contractReady
                      ? 'bg-purple-600 hover:bg-purple-500 text-white shadow-[0_0_20px_rgba(139,92,246,0.4)]'
                      : 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-[0_0_20px_rgba(79,70,229,0.4)]'
                    : 'bg-slate-800 text-slate-400 cursor-wait'}`}
              >
                {(txStatus === 'idle' || txStatus === 'success' || txStatus === 'error') && (
                  contract.contractReady
                    ? <><Zap className="w-5 h-5" /><span>链上注册资产 · 真实合约</span></>
                    : <><Zap className="w-5 h-5" /><span>模拟 B端大厂合规采买</span></>
                )}
                {txStatus === 'processing' && <><Activity className="w-5 h-5 animate-spin" /><span>{contract.contractReady ? '区块链交易广播中...' : 'AMM 撮合清算中...'}</span></>}
              </button>

              {/* 交易 Hash 链接 */}
              {contract.txHash && contract.txUrl && (
                <a
                  href={contract.txUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1.5 text-[10px] font-mono text-purple-400 hover:text-purple-300 mt-1.5 transition-colors"
                >
                  <ExternalLink className="w-3 h-3" />
                  在区块浏览器查看: {contract.txHash.slice(0, 20)}…
                </a>
              )}
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
                    {purchaseHistory.map((pt, i) => (
                      <ReferenceDot key={i} x={pt.demand} y={pt.price} r={4} fill={pt.label === 'TX' ? '#10b981' : '#f59e0b'} stroke="#0f172a" strokeWidth={1.5} label={{ value: pt.label, position: 'top', fontSize: 8, fill: '#94a3b8' }} />
                    ))}
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
