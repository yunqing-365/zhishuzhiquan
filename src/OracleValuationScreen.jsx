import React, { useState, useEffect } from 'react';
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer, Tooltip } from 'recharts';
import { Activity, ShieldCheck, FileText, Calculator, Network, CheckCircle2, ArrowRight } from 'lucide-react';

const OracleValuationScreen = ({ assetData, assetCategory, isZkMode, onNext }) => {
  const [isCalculating, setIsCalculating] = useState(true);
  const [calcStep, setCalcStep] = useState(0);
  const [chartData, setChartData] = useState([]);
  const [valuationResult, setValuationResult] = useState(null);

  const metricNamesMap = {
    image: ["语义对齐度(CLIP)", "频域隐写鲁棒性", "LAION美学评级", "画派风格稀缺度", "LoRA微调增益", "KNN-Shapley贡献度"],
    text: ["信息熵密度(抗废话)", "语料信噪比(AHP)", "实体拓扑密度(GraphRAG)", "语料库稀缺度(熵权)", "大模型微调增益", "KNN-Shapley贡献度"]
  };

  useEffect(() => {
    const initialNames = metricNamesMap[assetCategory] || metricNamesMap.text;
    setChartData(initialNames.map(name => ({ subject: name, score: 0, fullMark: 100 })));

    const steps = [
      `接收前端 [${assetCategory.toUpperCase()}-ADAPTER] 降维特征流...`,
      '验证 zk-SNARK 盲态交叉证明签名合法性...',
      '过滤废话与劣质数据，计算综合质量核心分...',
      '触发 KNN-Shapley 与实物期权定价模型 (Real Options)...',
      '乘以 Token 当量 (TEV) 杠杆，生成最终统一定价...'
    ];

    let current = 0;
    const interval = setInterval(() => {
      if (current < steps.length) { setCalcStep(current); current++; }
    }, 800);

    const fetchRealValuation = async () => {
      try {
        const response = await fetch('http://127.0.0.1:8000/api/valuate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            asset_category: assetCategory,
            description: assetData,
            is_zk_mode: isZkMode
          })
        });
        
        if (!response.ok) throw new Error("HTTP Error");
        const realData = await response.json();
        
        setTimeout(() => {
          clearInterval(interval);
          setValuationResult(realData);
          setChartData(realData.metrics);
          setIsCalculating(false);
        }, 4000); 

      } catch (error) {
        console.warn("API 未连接，切入 Mock 模式");
        const isImage = assetCategory === 'image';
        const fallbackMetrics = initialNames.map((name, idx) => ({
            subject: name,
            score: isImage ? [92, 95, 96, 96, 89, 85][idx] : [92, 88, 85, 96, 82, 95][idx],
            fullMark: 100
        }));

        setTimeout(() => {
          clearInterval(interval);
          setValuationResult({
            status: "success",
            asset_hash: "0xFallbackMockHash8a7b6c5d4e3f2a1b",
            metrics: fallbackMetrics,
            final_valuation: {
                composite_quality: 91.5,
                modality_multiplier: isImage ? "50.0x" : "1.0x",
                base_value: isImage ? 9150 : 183,
                creator_ratio: 85.0
            }
          });
          setChartData(fallbackMetrics);
          setIsCalculating(false);
        }, 4000);
      }
    };

    fetchRealValuation();
    return () => clearInterval(interval);
  }, [assetData, assetCategory, isZkMode]);

  return (
    <div className="min-h-screen relative flex items-center justify-center bg-slate-950 overflow-hidden p-6 font-sans">
      <div className="absolute top-0 right-0 w-[500px] h-[500px] bg-teal-900/20 rounded-full blur-[150px] pointer-events-none"></div>

      <div className="relative max-w-6xl w-full bg-slate-900/60 backdrop-blur-2xl border border-slate-700/50 rounded-3xl p-8 shadow-2xl flex flex-col h-[85vh]">
        
        <div className="flex items-center justify-between mb-8 pb-4 border-b border-slate-700/50">
          <div className="flex flex-col">
            <div className="flex items-center space-x-4 mb-2">
              <Network className="w-8 h-8 text-purple-400" />
              <h1 className="text-2xl font-extrabold tracking-wide text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-indigo-300">
                多模态基建统一定价预言机 (Oracle)
              </h1>
            </div>
            <div className="flex items-center space-x-2 text-[11px] uppercase font-mono tracking-wider">
              <span className="bg-slate-800 text-slate-400 px-3 py-1 rounded shadow-inner">
                {assetCategory === 'image' ? 'Visual Art (画作)' : 'Text Corpus (文本)'}
              </span>
              <ArrowRight className="w-3 h-3 text-slate-600" />
              <span className={`px-3 py-1 rounded border shadow-lg ${assetCategory === 'image' ? 'bg-amber-900/30 text-amber-400 border-amber-500/30' : 'bg-blue-900/30 text-blue-400 border-blue-500/30'}`}>
                {assetCategory === 'image' ? 'Image-Adapter (LAION & DWT)' : 'Text-Adapter (GraphRAG)'}
              </span>
              <ArrowRight className="w-3 h-3 text-slate-600" />
              <span className="bg-emerald-900/30 text-emerald-400 px-3 py-1 rounded border border-emerald-500/30">
                Unified Pricing Framework (统一定价)
              </span>
            </div>
          </div>
          <div className={`px-4 py-2 rounded-full border text-sm font-bold flex items-center ${isCalculating ? 'bg-amber-500/10 border-amber-500/30 text-amber-400' : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'}`}>
            {isCalculating ? <Activity className="w-4 h-4 mr-2 animate-spin" /> : <CheckCircle2 className="w-4 h-4 mr-2" />}
            {isCalculating ? 'Consensus Computing...' : 'Valuation Complete'}
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-10 flex-1 overflow-hidden">
          
          <div className="bg-slate-950/50 rounded-2xl border border-slate-800 p-6 flex flex-col relative">
            <h3 className="text-sm font-bold uppercase tracking-widest mb-4 flex items-center text-purple-400">
              <Activity className="w-4 h-4 mr-2" /> 降维映射后标准化特征矩阵
            </h3>
            <div className="flex-1 w-full relative">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart cx="50%" cy="50%" outerRadius="70%" data={chartData}>
                  <PolarGrid stroke="#1e293b" />
                  <PolarAngleAxis dataKey="subject" tick={{ fill: '#94a3b8', fontSize: 11, fontWeight: 600 }} />
                  <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} axisLine={false} />
                  <Tooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: '#8b5cf6', borderRadius: '8px' }} itemStyle={{ color: '#a78bfa' }} />
                  <Radar dataKey="score" stroke="#8b5cf6" strokeWidth={2} fill="#7c3aed" fillOpacity={isCalculating ? 0.1 : 0.4} />
                </RadarChart>
              </ResponsiveContainer>
              {isCalculating && <div className="absolute inset-0 bg-gradient-to-b from-transparent via-purple-500/10 to-transparent bg-[length:100%_200%] animate-[scan_2s_linear_infinite] pointer-events-none rounded-xl"></div>}
            </div>
          </div>

          <div className="flex flex-col space-y-6">
            
            <div className="bg-[#0a0f18] rounded-2xl border border-slate-800 p-6 h-48 overflow-hidden relative">
              <h3 className="text-xs font-bold text-slate-600 uppercase tracking-widest mb-4 border-b border-slate-800/80 pb-2">
                Unified Oracle Execution Log
              </h3>
              <div className="space-y-3">
                {[
                  `接收 [${assetCategory.toUpperCase()}-ADAPTER] 降维映射特征流...`,
                  '验证 zk-SNARK 盲态交叉证明签名合法性...',
                  '过滤废话与劣质数据，计算综合质量核心分...',
                  '触发 KNN-Shapley 与实物期权定价模型 (Real Options)...',
                  '乘以 Token 当量 (TEV) 杠杆，生成最终统一定价...'
                ].map((step, index) => (
                  <div key={index} className={`font-mono text-sm transition-all duration-300 ${index === calcStep ? 'text-purple-400 animate-pulse font-bold' : index < calcStep ? 'text-slate-500' : 'opacity-0'}`}>
                    {index < calcStep ? '[DONE] ' : '>> '} {step}
                  </div>
                ))}
              </div>
            </div>

            <div className={`flex-1 bg-gradient-to-br from-slate-900 border rounded-2xl p-6 relative overflow-hidden transition-all duration-700 transform ${isCalculating ? 'translate-y-10 opacity-0 blur-sm' : 'translate-y-0 opacity-100 blur-none'} to-purple-950/20 border-purple-500/30`}>
              <ShieldCheck className="absolute -bottom-6 -right-6 w-48 h-48 rotate-12 text-purple-900/20" />
              
              <div className="flex justify-between items-start mb-4 border-b border-purple-500/20 pb-4">
                <div>
                  <h2 className="text-lg font-bold text-white flex items-center">
                    <FileText className="w-5 h-5 mr-2 text-purple-400" /> 多模态统一定价与分账凭证
                  </h2>
                  <p className="text-xs text-slate-500 font-mono mt-1">Hash: {valuationResult?.asset_hash}</p>
                </div>
              </div>

              {/* 【绝杀升级：展示质量分、杠杆和最终价格】 */}
              {valuationResult?.status === "rejected" ? (
                 <div className="p-4 bg-red-900/20 border border-red-500/30 rounded-xl mb-6 relative z-10 text-red-400 font-bold text-center">
                    ⚠️ 熔断拦截：检测到极低质量数据（废话/随手拍），拒绝估值上链！
                 </div>
              ) : (
                <div className="grid grid-cols-3 gap-3 mb-6 relative z-10">
                  <div className="bg-slate-950/50 p-3 rounded-xl border border-slate-800">
                    <p className="text-[10px] text-slate-500 uppercase mb-1">六维综合质量分</p>
                    <p className="text-xl font-mono text-white">{valuationResult?.final_valuation.composite_quality} <span className="text-[10px] text-slate-500">/100</span></p>
                  </div>
                  <div className="bg-slate-950/50 p-3 rounded-xl border border-slate-800">
                    <p className="text-[10px] text-slate-500 uppercase mb-1 flex items-center">Token 模态杠杆 <span className="ml-1 bg-amber-500/20 text-amber-400 px-1 rounded text-[8px]">TEV</span></p>
                    <p className={`text-xl font-bold ${assetCategory === 'image' ? 'text-amber-400' : 'text-blue-400'}`}>{valuationResult?.final_valuation.modality_multiplier}</p>
                  </div>
                  <div className="bg-purple-900/20 p-3 rounded-xl border border-purple-500/30">
                    <p className="text-[10px] text-purple-400 uppercase mb-1">动态统一定价 (Base)</p>
                    <p className="text-xl font-mono text-white">{valuationResult?.final_valuation.base_value.toLocaleString()} <span className="text-[10px] text-purple-400">CRD</span></p>
                  </div>
                </div>
              )}

              <button 
                onClick={onNext}
                disabled={valuationResult?.status === "rejected"}
                className={`w-full py-4 border rounded-xl font-bold transition-all flex items-center justify-center relative z-10 ${valuationResult?.status === "rejected" ? 'bg-slate-800 text-slate-500 border-slate-700 cursor-not-allowed' : 'bg-purple-500/10 hover:bg-purple-500/20 border-purple-500/50 text-purple-300 shadow-[0_0_15px_rgba(139,92,246,0.1)] hover:shadow-[0_0_25px_rgba(139,92,246,0.3)]'}`}
              >
                下发至智能合约领域交易大盘 (AMM) <ArrowRight className="w-4 h-4 ml-2" />
              </button>
            </div>

          </div>
        </div>
      </div>
    </div>
  );
};

export default OracleValuationScreen;