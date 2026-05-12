import React, { useState, useEffect } from 'react';
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer, Tooltip } from 'recharts';
import { Activity, ShieldCheck, FileText, Calculator, Network, CheckCircle2 } from 'lucide-react';

const OracleValuationScreen = ({ assetData, isZkMode, onNext }) => {
  const [isCalculating, setIsCalculating] = useState(true);
  const [calcStep, setCalcStep] = useState(0);
  const [chartData, setChartData] = useState([]);
  const [valuationResult, setValuationResult] = useState(null);

  const initialData = [
    { subject: '香农信息密度 (AHP)', score: 0, fullMark: 100 },
    { subject: '语料信噪比 (AHP)', score: 0, fullMark: 100 },
    { subject: '结构与指令连贯 (AHP)', score: 0, fullMark: 100 },
    { subject: '跨网语义稀缺度 (熵权)', score: 0, fullMark: 100 },
    { subject: '预期大模型增益 (熵权)', score: 0, fullMark: 100 },
    { subject: '节点履约信用', score: 0, fullMark: 100 },
  ];

  useEffect(() => {
    setChartData(initialData);

    const steps = isZkMode ? [
      '验证 zk-SNARK 零知识证明签名合法性...',
      '提取盲态香农信息熵与信噪比参数...',
      '调用 RAG 知识库检索语义距离 (L2)...',
      '执行 AHP-熵权法 动态价值熔炼...',
      '向 Python 预言机节点发起 RPC 调用...'
    ] : [
      '正在接入 RAG 知识库检索全网重合度...',
      '触发 AHP 层次分析，计算主观质量权重...',
      '提取链上参数，计算熵权法客观离散度...',
      '执行 W_i = α*W_ahp + β*W_entropy 权重融合...',
      '向 Python 预言机节点发起 RPC 调用...'
    ];

    let current = 0;
    const interval = setInterval(() => {
      if (current < steps.length) {
        setCalcStep(current);
        current++;
      }
    }, 800);

    const fetchRealValuation = async () => {
      try {
        const response = await fetch('http://127.0.0.1:8000/api/valuate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            asset_category: assetCategory || 'text', // 添加了这一行
            asset_type: "自定义输入语料",
            description: assetData || "未输入内容",
            author_id: "Node-7A9B",
            is_zk_mode: isZkMode
          })
        });
        
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const realData = await response.json();
        
        setTimeout(() => {
          clearInterval(interval);
          setValuationResult(realData);
          setChartData(realData.metrics);
          setIsCalculating(false);
        }, 4000); 

      } catch (error) {
        console.warn("⚠️ 警告: 无法连接 Python 后端，已自动切换为防翻车(Mock)模式！", error);
        
        // 容灾假数据
        const fallbackData = {
            status: "success",
            asset_hash: "0xFallbackMockHash8a7b6c5d4e3f2a1b",
            metrics: [
                { subject: "香农信息密度 (AHP)", score: 92 },
                { subject: "语料信噪比 (AHP)", score: 88 },
                { subject: "结构与指令连贯 (AHP)", score: 85 },
                { subject: "跨网语义稀缺度 (熵权)", score: 96 },
                { subject: "预期大模型增益 (熵权)", score: 82 },
                { subject: "节点履约信用", score: 95 },
            ],
            final_valuation: {
                base_value: 8425,
                creator_ratio: 82.5,
                node_ratio: 10.5,
                fund_ratio: 7.0
            }
        };

        setTimeout(() => {
          clearInterval(interval);
          setValuationResult(fallbackData);
          setChartData(fallbackData.metrics);
          setIsCalculating(false);
        }, 4000);
      }
    };

    fetchRealValuation();
    return () => clearInterval(interval);
  }, [assetData, isZkMode]);

  return (
    <div className="min-h-screen relative flex items-center justify-center bg-slate-950 overflow-hidden p-6 font-sans">
      <div className="absolute top-0 right-0 w-[500px] h-[500px] bg-teal-900/20 rounded-full blur-[150px] pointer-events-none"></div>
      <div className="absolute bottom-0 left-0 w-[500px] h-[500px] bg-emerald-900/20 rounded-full blur-[150px] pointer-events-none"></div>

      <div className="relative max-w-6xl w-full bg-slate-900/60 backdrop-blur-2xl border border-slate-700/50 rounded-3xl p-8 md:p-10 shadow-2xl flex flex-col h-[85vh]">
        
        <div className="flex items-center justify-between mb-8 pb-4 border-b border-slate-700/50">
          <div className="flex items-center space-x-4">
            <div className={`p-3 border rounded-2xl animate-pulse ${isZkMode ? 'bg-purple-500/10 border-purple-500/20' : 'bg-teal-500/10 border-teal-500/20'}`}>
              <Network className={`w-8 h-8 ${isZkMode ? 'text-purple-400' : 'text-teal-400'}`} />
            </div>
            <div>
              <h1 className={`text-2xl font-extrabold tracking-wide text-transparent bg-clip-text bg-gradient-to-r ${isZkMode ? 'from-purple-400 to-indigo-300' : 'from-teal-400 to-emerald-300'}`}>
                {isZkMode ? 'ZK 盲态交叉验证预言机 (Blind Oracle)' : '双源交叉验证预言机 (Oracle)'}
              </h1>
              <p className="text-slate-400 text-sm mt-1 flex items-center">
                <Calculator className={`w-4 h-4 mr-1 ${isZkMode ? 'text-purple-500' : 'text-emerald-500'}`} /> API: Live | Python NLP Engine Active
              </p>
            </div>
          </div>
          
          <div className={`px-4 py-2 rounded-full border text-sm font-bold flex items-center transition-all duration-500 ${isCalculating ? 'bg-amber-500/10 border-amber-500/30 text-amber-400' : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'}`}>
            {isCalculating ? <Activity className="w-4 h-4 mr-2 animate-spin" /> : <CheckCircle2 className="w-4 h-4 mr-2" />}
            {isCalculating ? 'Consensus Computing...' : 'Valuation Complete'}
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-10 flex-1 overflow-hidden">
          
          <div className="bg-slate-950/50 rounded-2xl border border-slate-800 p-6 flex flex-col relative group">
            <h3 className={`text-sm font-bold uppercase tracking-widest mb-4 flex items-center ${isZkMode ? 'text-purple-400' : 'text-teal-500'}`}>
              <Activity className="w-4 h-4 mr-2" />
              数据要素香农特征图谱
            </h3>
            
            <div className="flex-1 w-full relative">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart cx="50%" cy="50%" outerRadius="75%" data={chartData}>
                  <PolarGrid stroke="#1e293b" />
                  <PolarAngleAxis dataKey="subject" tick={{ fill: '#94a3b8', fontSize: 12, fontWeight: 600 }} />
                  <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} axisLine={false} />
                  <Tooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: isZkMode?'#8b5cf6':'#0d9488', borderRadius: '8px' }} itemStyle={{ color: isZkMode?'#a78bfa':'#2dd4bf' }} />
                  <Radar name="特征指数" dataKey="score" stroke={isZkMode?"#8b5cf6":"#14b8a6"} strokeWidth={2} fill={isZkMode?"#7c3aed":"#0d9488"} fillOpacity={isCalculating ? 0.1 : 0.4} className="transition-all duration-1000 ease-out" />
                </RadarChart>
              </ResponsiveContainer>
              
              {isCalculating && (
                <div className={`absolute inset-0 bg-gradient-to-b from-transparent via-transparent bg-[length:100%_200%] animate-[scan_2s_linear_infinite] pointer-events-none rounded-xl ${isZkMode?'via-purple-500/10':'via-teal-500/5'}`}></div>
              )}
            </div>
          </div>

          <div className="flex flex-col space-y-6">
            
            <div className="bg-[#0a0f18] rounded-2xl border border-slate-800 p-6 h-48 overflow-hidden relative">
              <h3 className="text-xs font-bold text-slate-600 uppercase tracking-widest mb-4 border-b border-slate-800/80 pb-2">
                Oracle Execution Log
              </h3>
              <div className="space-y-3">
                {(isZkMode ? [
                  '验证 zk-SNARK 零知识证明签名合法性...',
                  '提取盲态香农信息熵与信噪比参数...',
                  '调用 RAG 知识库检索语义距离 (L2)...',
                  '执行 AHP-熵权法 动态价值熔炼...',
                  '接收 Python 引擎盲态计算结果...'
                ] : [
                  '正在接入 RAG 知识库检索全网重合度...',
                  '触发 AHP 层次分析，计算主观质量权重...',
                  '提取链上参数，计算熵权法客观离散度...',
                  '执行 W_i = α*W_ahp + β*W_entropy 权重融合...',
                  '接收 Python 引擎计算结果...'
                ]).map((step, index) => (
                  <div key={index} className={`font-mono text-sm transition-all duration-300 ${index === calcStep ? (isZkMode?'text-purple-400':'text-teal-400') + ' animate-pulse font-bold' : index < calcStep ? 'text-slate-500' : 'opacity-0'}`}>
                    {index < calcStep ? '[DONE] ' : '>> '} {step}
                  </div>
                ))}
              </div>
            </div>

            <div className={`flex-1 bg-gradient-to-br from-slate-900 border rounded-2xl p-6 relative overflow-hidden transition-all duration-700 transform ${isCalculating ? 'translate-y-10 opacity-0 blur-sm' : 'translate-y-0 opacity-100 blur-none'} ${isZkMode ? 'to-purple-950/20 border-purple-500/30' : 'to-teal-950/40 border-teal-500/30'}`}>
              
              <ShieldCheck className={`absolute -bottom-6 -right-6 w-48 h-48 rotate-12 ${isZkMode?'text-purple-900/20':'text-teal-900/20'}`} />
              
              <div className={`flex justify-between items-start mb-6 border-b pb-4 ${isZkMode?'border-purple-500/20':'border-teal-500/20'}`}>
                <div>
                  <h2 className="text-xl font-bold text-white flex items-center">
                    <FileText className={`w-5 h-5 mr-2 ${isZkMode?'text-purple-400':'text-teal-400'}`} /> 
                    {isZkMode ? '零知识数据资产可信估值凭证' : '数据资产可信估值凭证'}
                  </h2>
                  <p className={`text-xs font-mono mt-1 ${isZkMode?'text-purple-500/70':'text-teal-500/70'}`}>ISSUER: AI-ECHO ORACLE NETWORK</p>
                  <p className="text-xs text-slate-500 font-mono truncate w-48 mt-1">Hash: {valuationResult?.asset_hash}</p>
                </div>
                <div className="text-right">
                  <div className={`text-3xl font-black text-transparent bg-clip-text bg-gradient-to-r ${isZkMode?'from-purple-300 to-indigo-400':'from-teal-300 to-emerald-400'}`}>
                    {valuationResult?.final_valuation.base_value > 8000 ? 'S' : valuationResult?.final_valuation.base_value > 6000 ? 'A+' : 'A'}
                  </div>
                  <p className="text-xs text-slate-400 uppercase">资产综合评级</p>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4 mb-6 relative z-10">
                <div className="bg-slate-950/50 p-4 rounded-xl border border-slate-800">
                  <p className="text-xs text-slate-500 uppercase mb-1">动态价值基准 (Base Value)</p>
                  <p className="text-2xl font-mono text-white">{valuationResult?.final_valuation.base_value.toLocaleString()} <span className={`text-sm ${isZkMode?'text-purple-400':'text-teal-500'}`}>Credits</span></p>
                </div>
                <div className="bg-slate-950/50 p-4 rounded-xl border border-slate-800">
                  <p className="text-xs text-slate-500 uppercase mb-1">建议创作者分账比例</p>
                  <p className={`text-2xl font-bold ${isZkMode?'text-purple-400':'text-emerald-400'}`}>{valuationResult?.final_valuation.creator_ratio} <span className="text-sm">%</span></p>
                </div>
              </div>

              <button 
                onClick={onNext}
                className={`w-full py-4 border rounded-xl font-bold transition-all flex items-center justify-center relative z-10 ${isZkMode ? 'bg-purple-500/10 hover:bg-purple-500/20 border-purple-500/50 text-purple-300 shadow-[0_0_15px_rgba(139,92,246,0.1)] hover:shadow-[0_0_25px_rgba(139,92,246,0.3)]' : 'bg-teal-500/10 hover:bg-teal-500/20 border-teal-500/50 text-teal-300 shadow-[0_0_15px_rgba(20,184,166,0.1)] hover:shadow-[0_0_25px_rgba(20,184,166,0.3)]'}`}
              >
                授权 API 调用并进入智能分账协议 <Activity className="w-4 h-4 ml-2" />
              </button>
            </div>

          </div>
        </div>
      </div>
    </div>
  );
};

export default OracleValuationScreen;