import React, { useState, useEffect } from 'react';
import { Wallet, ShieldCheck, Database, Link as LinkIcon, Hexagon, Zap, CheckCircle, Activity, Server, TrendingUp } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceDot, ReferenceLine } from 'recharts';

// 为了 Demo 流畅，如果没有传入 props，我们提供默认的测试参数
const SmartSplitScreen = ({ 
  assetCategory = 'image', 
  baseValue = 9250, 
  creatorRatio = 85.0,
  onRestart 
}) => {
  const [txStatus, setTxStatus] = useState('idle'); 
  const [logs, setLogs] = useState([]);
  
  // 【核心升级】：根据不同模态，匹配不同的买家和涨价斜率 (Alpha)
  const isImage = assetCategory === 'image';
  const alpha = isImage ? 15 : 25; // 医疗文本(25)涨价快，画作(15)涨价平稳
  const domainName = isImage ? 'Visual Art (商业视觉原画)' : 'Medical SFT (垂直医疗语料)';
  const b2bCaller = isImage ? 'Midjourney-LoRA-Trainer' : 'Medical-LLM-Corp';
  
  const [demand, setDemand] = useState(20); 
  const [currentPrice, setCurrentPrice] = useState(0);
  const [curveData, setCurveData] = useState([]);

  // 计算联合曲线价格公式
  const calculatePrice = (d) => Math.round(baseValue * (1000 + (d * alpha)) / 1000);

  useEffect(() => {
    const data = [];
    for(let i = 0; i <= 50; i += 2) {
      data.push({ demand: i, price: calculatePrice(i) });
    }
    setCurveData(data);
    setCurrentPrice(calculatePrice(demand));
  }, [demand, assetCategory, baseValue]);

  const addLog = (msg) => setLogs(prev => [...prev, msg]);

  const handleSimulatePayment = () => {
    setTxStatus('processing');
    setLogs([]);
    
    // 【核心升级】：根据模态展示截然不同的 B 端业务调用日志
    if (isImage) {
      setTimeout(() => addLog(`>> [B端大厂节点] ${b2bCaller} 正在全网抓取赛博朋克画风数据集...`), 500);
      setTimeout(() => addLog(`>> [AI Paywall] ⚠️ 拦截成功！检测到大厂未授权“融图”行为 (Hash: 0xDCT_Image...)`), 1500);
    } else {
      setTimeout(() => addLog(`>> [B端大厂节点] ${b2bCaller} 正在检索罕见病理结构化语料...`), 500);
      setTimeout(() => addLog(`>> [AI Paywall] ⚠️ 拦截成功！检测到大模型未授权 RAG 抓取 (Hash: 0xSimHash...)`), 1500);
    }
    
    setTimeout(() => addLog(`>> [智能合约] 大厂调用 purchaseAndCallData()，支付 Token 当量定价，AI-Echo 释放授权凭证...`), 2800);
    
    setTimeout(() => {
      const newDemand = demand + 1;
      const newPrice = calculatePrice(newDemand);
      setDemand(newDemand);
      setCurrentPrice(newPrice);
      addLog(`>> [AMM 联合曲线] ${domainName} 领域热度上升，该资产调用费自动上调至 ${newPrice.toLocaleString()} CRD 📈`);
    }, 4500);

    setTimeout(() => addLog(`>> [结算网关] 正在执行去中心化跨链分账 (创作者占比: ${creatorRatio}%)...`), 5500);
    setTimeout(() => {
      addLog('>> [SUCCESS] 创作者已获得本次大模型训练的调用分润！合规流水上链完成。');
      setTxStatus('success');
    }, 6500);
  };

  return (
    <div className="min-h-screen relative flex items-center justify-center bg-[#050b14] overflow-hidden p-6 font-sans">
      <div className="absolute inset-0 bg-[linear-gradient(to_right,#1e293b_1px,transparent_1px),linear-gradient(to_bottom,#1e293b_1px,transparent_1px)] bg-[size:4rem_4rem] [mask-image:radial-gradient(ellipse_60%_60%_at_50%_50%,#000_70%,transparent_100%)] opacity-20"></div>
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[600px] bg-purple-900/10 rounded-full blur-[150px] pointer-events-none"></div>

      <div className="relative max-w-6xl w-full bg-slate-900/80 backdrop-blur-xl border border-slate-700/50 rounded-3xl p-8 md:p-10 shadow-2xl flex flex-col">
        
        <div className="flex items-center justify-between mb-8 pb-4 border-b border-slate-700/50">
          <div className="flex flex-col">
            <div className="flex items-center space-x-4 mb-2">
              <div className="p-3 bg-purple-500/10 border border-purple-500/20 rounded-2xl">
                <LinkIcon className="w-8 h-8 text-purple-400" />
              </div>
              <h1 className="text-2xl font-extrabold tracking-wide text-white">SmartSplit·去中心化语料交易大盘</h1>
            </div>
            <p className="text-slate-400 text-[11px] font-mono flex items-center bg-slate-950 px-3 py-1 rounded-full border border-slate-800 w-fit">
              <ShieldCheck className="w-3 h-3 mr-2 text-purple-500" /> 
              Domain: {domainName} | AMM Alpha: {alpha} | Bonding Curve Pricing
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
                <div className="p-3 bg-slate-900 rounded-lg border border-slate-800">
                  <p className="text-[10px] text-slate-500 mb-1 uppercase">B端买方 / 调用方 (API Caller)</p>
                  <p className="text-sm text-white font-mono flex items-center">
                    <Hexagon className={`w-4 h-4 mr-2 ${isImage ? 'text-amber-500' : 'text-blue-500'}`} /> 
                    {b2bCaller}
                  </p>
                </div>
                <div className="p-4 bg-slate-900 rounded-xl border border-purple-500/30 flex justify-between items-center bg-purple-950/20 shadow-[0_0_15px_rgba(168,85,247,0.1)]">
                  <div>
                    <p className="text-[10px] text-purple-400/80 uppercase">当前联合曲线实时报价</p>
                    <p className="text-xs text-slate-400 font-mono mt-1">Base: {baseValue.toLocaleString()} | Dem: {demand}</p>
                  </div>
                  <p className="text-2xl font-bold text-purple-400 font-mono transition-all duration-500 transform">
                    {currentPrice.toLocaleString()} <span className="text-xs text-purple-500/50">CRD</span>
                  </p>
                </div>
              </div>
              <button 
                onClick={handleSimulatePayment} disabled={txStatus === 'processing'}
                className={`w-full py-4 rounded-xl font-bold flex items-center justify-center space-x-2 transition-all duration-300 ${txStatus === 'idle' || txStatus === 'success' ? 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-[0_0_20px_rgba(79,70,229,0.4)]' : 'bg-slate-800 text-slate-400 cursor-wait'}`}
              >
                {(txStatus === 'idle' || txStatus === 'success') && <><Zap className="w-5 h-5" /> <span>模拟 B端大厂合规采买</span></>}
                {txStatus === 'processing' && <><Activity className="w-5 h-5 animate-spin" /> <span>AMM 撮合清算中...</span></>}
              </button>
            </div>

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

          {/* 右侧可视化图表与分账模块 */}
          <div className="lg:col-span-8 bg-slate-950/50 border border-slate-800 rounded-2xl p-6 relative flex flex-col justify-between shadow-lg">
            
            {/* AMM 联合曲线可视化 */}
            <div className="mb-6">
              <h3 className="text-sm font-bold text-slate-300 flex items-center mb-4">
                <TrendingUp className="w-4 h-4 mr-2 text-purple-400" />
                资产流动性与联合曲线 (Bonding Curve)
              </h3>
              <div className="h-48 w-full bg-slate-900/50 rounded-xl p-2 border border-slate-800/50">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={curveData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                    <defs>
                      <linearGradient id="colorPrice" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.5}/>
                        <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0}/>
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
                <p className="text-2xl font-mono font-bold text-white transition-all">{txStatus === 'idle' ? '0' : currentPrice.toLocaleString()}</p>
              </div>
              
              {/* 分账动画连线 */}
              <div className="h-16 w-full relative flex justify-center">
                <div className={`absolute top-0 w-[2px] h-8 bg-slate-700 transition-colors ${txStatus === 'success' ? 'bg-purple-500' : ''}`}></div>
                <div className={`absolute top-8 w-[70%] h-[2px] bg-slate-700 transition-colors ${txStatus === 'success' ? 'bg-purple-500' : ''}`}></div>
                <div className={`absolute top-8 left-[15%] w-[2px] h-8 bg-slate-700 transition-colors ${txStatus === 'success' ? 'bg-purple-500' : ''}`}></div>
                <div className={`absolute top-8 left-[50%] w-[2px] h-8 bg-slate-700 transition-colors ${txStatus === 'success' ? 'bg-purple-500' : ''}`}></div>
                <div className={`absolute top-8 right-[15%] w-[2px] h-8 bg-slate-700 transition-colors ${txStatus === 'success' ? 'bg-purple-500' : ''}`}></div>
              </div>

              {/* 三方收益方 */}
              <div className="w-full flex justify-between px-2">
                <div className={`w-[32%] flex flex-col items-center p-4 rounded-xl border transition-all duration-500 ${txStatus === 'success' ? 'bg-emerald-950/40 border-emerald-500/50 shadow-[0_0_15px_rgba(16,185,129,0.1)]' : 'bg-slate-900 border-slate-800'}`}>
                  <Wallet className={`w-5 h-5 mb-2 ${txStatus === 'success' ? 'text-emerald-400' : 'text-slate-600'}`} />
                  <p className="text-[10px] text-slate-400 text-center mb-1 uppercase">创作者分红 ({creatorRatio}%)</p>
                  <p className={`text-lg font-mono font-bold ${txStatus === 'success' ? 'text-emerald-400' : 'text-slate-600'}`}>{txStatus === 'success' ? `+ ${(currentPrice * (creatorRatio/100)).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}` : '0.00'}</p>
                </div>
                <div className={`w-[32%] flex flex-col items-center p-4 rounded-xl border transition-all duration-500 delay-100 ${txStatus === 'success' ? 'bg-blue-950/40 border-blue-500/50 shadow-[0_0_15px_rgba(59,130,246,0.1)]' : 'bg-slate-900 border-slate-800'}`}>
                  <Activity className={`w-5 h-5 mb-2 ${txStatus === 'success' ? 'text-blue-400' : 'text-slate-600'}`} />
                  <p className="text-[10px] text-slate-400 text-center mb-1 uppercase">平台服务费 ({(100-creatorRatio)*0.6}%)</p>
                  <p className={`text-lg font-mono font-bold ${txStatus === 'success' ? 'text-blue-400' : 'text-slate-600'}`}>{txStatus === 'success' ? `+ ${(currentPrice * ((100-creatorRatio)/100) * 0.6).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}` : '0.00'}</p>
                </div>
                <div className={`w-[32%] flex flex-col items-center p-4 rounded-xl border transition-all duration-500 delay-200 ${txStatus === 'success' ? 'bg-amber-950/40 border-amber-500/50 shadow-[0_0_15px_rgba(245,158,11,0.1)]' : 'bg-slate-900 border-slate-800'}`}>
                  <ShieldCheck className={`w-5 h-5 mb-2 ${txStatus === 'success' ? 'text-amber-400' : 'text-slate-600'}`} />
                  <p className="text-[10px] text-slate-400 text-center mb-1 uppercase">社区共建基金 ({(100-creatorRatio)*0.4}%)</p>
                  <p className={`text-lg font-mono font-bold ${txStatus === 'success' ? 'text-amber-400' : 'text-slate-600'}`}>{txStatus === 'success' ? `+ ${(currentPrice * ((100-creatorRatio)/100) * 0.4).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}` : '0.00'}</p>
                </div>
              </div>
            </div>

          </div>
        </div>
      </div>
    </div>
  );
};

export default SmartSplitScreen;