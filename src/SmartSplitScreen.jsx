import React, { useState, useEffect } from 'react';
import { Wallet, ShieldCheck, Database, Link as LinkIcon, Hexagon, Zap, CheckCircle, Activity, Server, TrendingUp } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceDot, ReferenceLine } from 'recharts';

const SmartSplitScreen = ({ onRestart }) => {
  const [txStatus, setTxStatus] = useState('idle'); 
  const [logs, setLogs] = useState([]);
  
  // ==========================================
  // AMM 联合曲线动态状态 (同步智能合约逻辑)
  // ==========================================
  const baseValue = 8425; // 预言机给出的基础内在价值
  const alpha = 15; // 涨幅系数 (每次调用涨价 1.5%)
  
  const [demand, setDemand] = useState(20); // 初始模拟已经被调用了 20 次
  const [currentPrice, setCurrentPrice] = useState(0);
  const [curveData, setCurveData] = useState([]);

  // 计算联合曲线价格公式: Price = Base * (1000 + (Demand * Alpha)) / 1000
  const calculatePrice = (d) => Math.round(baseValue * (1000 + (d * alpha)) / 1000);

  // 初始化生成曲线图表数据
  useEffect(() => {
    const data = [];
    // 生成 X 轴需求量从 0 到 50 的预测曲线数据
    for(let i = 0; i <= 50; i += 2) {
      data.push({ demand: i, price: calculatePrice(i) });
    }
    setCurveData(data);
    setCurrentPrice(calculatePrice(demand));
  }, [demand]);

  const addLog = (msg) => setLogs(prev => [...prev, msg]);

  const handleSimulatePayment = () => {
    setTxStatus('processing');
    setLogs([]);
    setTimeout(() => addLog('>> [终端用户] 询问 ChatGPT："请根据最新研究分析该重症风险..."'), 500);
    setTimeout(() => addLog(`>> [AI RAG 引擎] 正在全网检索，抓取到创作者独家博客文章 (Hash: 0x8a7b...)`), 1500);
    setTimeout(() => addLog(`>> [AI Paywall] ⚠️ 拦截成功！检测到大模型未授权抓取，要求支付基础检索费。`), 2500);
    setTimeout(() => addLog('>> [智能合约] B端触发 triggerRagMicroPayment()，AI-Echo 释放解密密钥...'), 3500);
    
    setTimeout(() => {
      // 模拟链上状态更新：需求量 +1，价格上涨
      const newDemand = demand + 1;
      const newPrice = calculatePrice(newDemand);
      setDemand(newDemand);
      setCurrentPrice(newPrice);
      addLog(`>> [AMM 联合曲线] 该独家知识点热度上升，RAG 基础检索费上调至 ${newPrice} CRD 📈`);
    }, 4500);

    setTimeout(() => addLog('>> [结算网关] 正在执行无感跨链分账 (创作者: 82.5%, 节点: 10.5%)...'), 5500);
    setTimeout(() => {
      addLog('>> [SUCCESS] 创作者已获得本次 AI 回答引用分润！流水上链完成。');
      setTxStatus('success');
    }, 6500);
  };

  return (
    <div className="min-h-screen relative flex items-center justify-center bg-[#050b14] overflow-hidden p-6 font-sans">
      <div className="absolute inset-0 bg-[linear-gradient(to_right,#1e293b_1px,transparent_1px),linear-gradient(to_bottom,#1e293b_1px,transparent_1px)] bg-[size:4rem_4rem] [mask-image:radial-gradient(ellipse_60%_60%_at_50%_50%,#000_70%,transparent_100%)] opacity-20"></div>
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[600px] bg-emerald-900/10 rounded-full blur-[150px] pointer-events-none"></div>

      <div className="relative max-w-6xl w-full bg-slate-900/80 backdrop-blur-xl border border-slate-700/50 rounded-3xl p-8 md:p-10 shadow-2xl flex flex-col">
        <div className="flex items-center justify-between mb-8 pb-4 border-b border-slate-700/50">
          <div className="flex items-center space-x-4">
            <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-2xl">
              <LinkIcon className="w-8 h-8 text-emerald-400" />
            </div>
            <div>
              <h1 className="text-2xl font-extrabold tracking-wide text-white">SmartSplit 数据资产自动做市商 (AMM)</h1>
              <p className="text-slate-400 text-sm mt-1 flex items-center font-mono">
                <ShieldCheck className="w-4 h-4 mr-1 text-emerald-500" /> Bonding Curve Pricing | Trustless Settlement
              </p>
            </div>
          </div>
          <button onClick={onRestart} className="text-sm text-slate-500 hover:text-emerald-400 transition-colors border border-slate-700 hover:border-emerald-500/50 px-4 py-2 rounded-lg">
            返回首页重置 Demo
          </button>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 relative z-10">
          
          {/* 左侧控制台 */}
          <div className="lg:col-span-4 flex flex-col space-y-6">
            <div className="bg-slate-950/80 border border-slate-800 rounded-2xl p-6 relative overflow-hidden">
              <h3 className="text-sm font-bold text-slate-400 uppercase tracking-widest mb-4 flex items-center"><Server className="w-4 h-4 mr-2 text-blue-400" /> 模拟大模型 RAG 检索拦截</h3>
              <div className="space-y-4 mb-6">
                <div className="p-3 bg-slate-900 rounded-lg border border-slate-800">
                  <p className="text-xs text-slate-500 mb-1">调用方 (API Caller)</p>
                  <p className="text-sm text-white font-mono flex items-center"><Hexagon className="w-4 h-4 mr-2 text-blue-500" /> Medical-LLM-Corp</p>
                </div>
                <div className="p-3 bg-slate-900 rounded-lg border border-emerald-500/30 flex justify-between items-center bg-emerald-950/20">
                  <div>
                    <p className="text-xs text-emerald-500/70">当前联合曲线动态报价</p>
                    <p className="text-xs text-slate-500 font-mono mt-1">Base: {baseValue} | Demand: {demand}</p>
                  </div>
                  <p className="text-xl font-bold text-emerald-400 font-mono transition-all duration-500 transform">{currentPrice.toLocaleString()} <span className="text-xs text-emerald-500/50">CRD</span></p>
                </div>
              </div>
              <button 
                onClick={handleSimulatePayment} disabled={txStatus === 'processing'}
                className={`w-full py-4 rounded-xl font-bold flex items-center justify-center space-x-2 transition-all duration-300 ${txStatus === 'idle' || txStatus === 'success' ? 'bg-blue-600 hover:bg-blue-500 text-white shadow-[0_0_20px_rgba(37,99,235,0.4)]' : 'bg-slate-800 text-slate-400 cursor-wait'}`}
              >
                {(txStatus === 'idle' || txStatus === 'success') && <><Zap className="w-5 h-5" /> <span>模拟 C端提问触发 RAG 微支付</span></>}
                {txStatus === 'processing' && <><Activity className="w-5 h-5 animate-spin" /> <span>AMM 撮合清算中...</span></>}
              </button>
            </div>

            <div className="flex-1 bg-black/60 border border-slate-800 rounded-2xl p-5 overflow-y-auto max-h-64">
              <h3 className="text-xs font-bold text-slate-600 uppercase tracking-widest mb-3">Contract Execution Logs</h3>
              <div className="space-y-2">
                {logs.map((log, i) => <p key={i} className={`text-xs font-mono leading-relaxed ${log.includes('SUCCESS') ? 'text-emerald-400 font-bold' : log.includes('AMM') ? 'text-blue-400' : 'text-slate-400'}`}>{log}</p>)}
                {txStatus === 'processing' && <p className="text-xs font-mono text-emerald-500/50 animate-pulse">_</p>}
              </div>
            </div>
          </div>

          {/* 右侧可视化图表与分账模块 */}
          <div className="lg:col-span-8 bg-slate-950/50 border border-slate-800 rounded-2xl p-6 relative flex flex-col justify-between">
            
            {/* AMM 联合曲线可视化 */}
            <div className="mb-6">
              <h3 className="text-sm font-bold text-slate-300 flex items-center mb-4">
                <TrendingUp className="w-4 h-4 mr-2 text-emerald-400" />
                资产流动性与联合曲线 (Bonding Curve)
              </h3>
              <div className="h-48 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={curveData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                    <defs>
                      <linearGradient id="colorPrice" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#10b981" stopOpacity={0.4}/>
                        <stop offset="95%" stopColor="#10b981" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                    <XAxis dataKey="demand" stroke="#475569" fontSize={10} tickLine={false} axisLine={false} />
                    <YAxis stroke="#475569" fontSize={10} tickLine={false} axisLine={false} />
                    <Tooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b' }} itemStyle={{ color: '#10b981' }} />
                    <Area type="monotone" dataKey="price" stroke="#10b981" strokeWidth={3} fillOpacity={1} fill="url(#colorPrice)" animationDuration={500} />
                    {/* 标记当前所在位置 */}
                    <ReferenceLine x={demand} stroke="#3b82f6" strokeDasharray="3 3" />
                    <ReferenceDot x={demand} y={currentPrice} r={6} fill="#10b981" stroke="#fff" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* 分账清算流 */}
            <div className="relative mt-8">
              <div className={`mx-auto z-10 p-4 rounded-2xl border-2 flex flex-col items-center w-56 bg-slate-900 transition-all duration-500 ${txStatus === 'processing' ? 'border-emerald-500 shadow-[0_0_20px_rgba(16,185,129,0.3)] scale-105' : 'border-slate-700'}`}>
                <Database className={`w-8 h-8 mb-2 ${txStatus === 'processing' ? 'text-emerald-400 animate-pulse' : 'text-slate-500'}`} />
                <p className="text-xs text-slate-400 mb-1">本次账单总额进入合约</p>
                <p className="text-xl font-mono font-bold text-white transition-all">{txStatus === 'idle' ? '0' : currentPrice.toLocaleString()}</p>
              </div>
              
              {/* 分账动画连线 */}
              <div className="h-16 w-full relative flex justify-center">
                <div className={`absolute top-0 w-[2px] h-8 bg-slate-700 transition-colors ${txStatus === 'success' ? 'bg-emerald-500' : ''}`}></div>
                <div className={`absolute top-8 w-[70%] h-[2px] bg-slate-700 transition-colors ${txStatus === 'success' ? 'bg-emerald-500' : ''}`}></div>
                <div className={`absolute top-8 left-[15%] w-[2px] h-8 bg-slate-700 transition-colors ${txStatus === 'success' ? 'bg-emerald-500' : ''}`}></div>
                <div className={`absolute top-8 left-[50%] w-[2px] h-8 bg-slate-700 transition-colors ${txStatus === 'success' ? 'bg-emerald-500' : ''}`}></div>
                <div className={`absolute top-8 right-[15%] w-[2px] h-8 bg-slate-700 transition-colors ${txStatus === 'success' ? 'bg-emerald-500' : ''}`}></div>
              </div>

              {/* 三方收益方 */}
              <div className="w-full flex justify-between px-2">
                <div className={`w-[32%] flex flex-col items-center p-4 rounded-xl border transition-all duration-500 ${txStatus === 'success' ? 'bg-emerald-950/40 border-emerald-500/50' : 'bg-slate-900 border-slate-800'}`}>
                  <Wallet className={`w-5 h-5 mb-2 ${txStatus === 'success' ? 'text-emerald-400' : 'text-slate-600'}`} />
                  <p className="text-xs text-slate-400 text-center mb-1">创作者分成 (82.5%)</p>
                  <p className={`text-sm md:text-lg font-mono font-bold ${txStatus === 'success' ? 'text-emerald-400' : 'text-slate-600'}`}>{txStatus === 'success' ? `+ ${(currentPrice * 0.825).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}` : '0.00'}</p>
                </div>
                <div className={`w-[32%] flex flex-col items-center p-4 rounded-xl border transition-all duration-500 delay-100 ${txStatus === 'success' ? 'bg-blue-950/40 border-blue-500/50' : 'bg-slate-900 border-slate-800'}`}>
                  <Activity className={`w-5 h-5 mb-2 ${txStatus === 'success' ? 'text-blue-400' : 'text-slate-600'}`} />
                  <p className="text-xs text-slate-400 text-center mb-1">预言机节点 (10.5%)</p>
                  <p className={`text-sm md:text-lg font-mono font-bold ${txStatus === 'success' ? 'text-blue-400' : 'text-slate-600'}`}>{txStatus === 'success' ? `+ ${(currentPrice * 0.105).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}` : '0.00'}</p>
                </div>
                <div className={`w-[32%] flex flex-col items-center p-4 rounded-xl border transition-all duration-500 delay-200 ${txStatus === 'success' ? 'bg-purple-950/40 border-purple-500/50' : 'bg-slate-900 border-slate-800'}`}>
                  <ShieldCheck className={`w-5 h-5 mb-2 ${txStatus === 'success' ? 'text-purple-400' : 'text-slate-600'}`} />
                  <p className="text-xs text-slate-400 text-center mb-1">社区治理基金 (7.0%)</p>
                  <p className={`text-sm md:text-lg font-mono font-bold ${txStatus === 'success' ? 'text-purple-400' : 'text-slate-600'}`}>{txStatus === 'success' ? `+ ${(currentPrice * 0.07).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}` : '0.00'}</p>
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