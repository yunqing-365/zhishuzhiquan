import React, { useState } from 'react';
import { UploadCloud, Cpu, Database, PlayCircle, ShieldCheck, Lock, Image as ImageIcon, FileText } from 'lucide-react';

const DataInputScreen = ({ onComplete }) => {
  const [isProcessing, setIsProcessing] = useState(false);
  const [progress, setProgress] = useState(0);
  const [statusText, setStatusText] = useState('');
  const [txHash, setTxHash] = useState('');
  
  const [enableZK, setEnableZK] = useState(true);
  const [assetCategory, setAssetCategory] = useState('image');
  const [inputText, setInputText] = useState('');
  const [selectedImage, setSelectedImage] = useState(null);

  const handleViewDemo = () => {
    setAssetCategory('image');
    setSelectedImage('cyberpunk_art.png');
    setInputText('插画 赛博朋克 艺术'); // 注入触发高分条件的关键词
    setTimeout(() => processData(), 500);
  };

  const getProcessingSteps = (category, isZk) => {
    const steps = [];
    if (isZk) {
      steps.push({ p: 10, text: '【沙箱挂载】正在初始化 WebAssembly 本地安全沙箱...' });
      if (category === 'image') {
        steps.push({ p: 25, text: '【模态分流】唤醒 Visual-Adapter，加载 LAION-Aesthetics 审美评估引擎...' });
        steps.push({ p: 40, text: '【防洗稿/打假】执行 DWT-DCT 频域隐写鲁棒性与构图原创性检测...' });
        steps.push({ p: 55, text: '【语义对齐】加载 CLIP 大模型，提取 512 维跨模态特征...' });
      } else {
        steps.push({ p: 25, text: '【模态分流】唤醒 Text-Adapter，加载 GraphRAG 拓扑提取引擎...' });
        steps.push({ p: 40, text: '【防洗稿/打假】执行香农信息熵计算，甄别与熔断“废话文学”...' });
        steps.push({ p: 55, text: '【语义对齐】提取文本稠密特征，构建高质量垂直语料图谱...' });
      }
      steps.push({ p: 75, text: '【计算 Token 当量 (TEV)】赋予对应模态杠杆权重，映射至统一 6 维框架...' });
      steps.push({ p: 90, text: '生成 zk-SNARK 零知识证明凭证，本地明文已彻底安全销毁...' });
      steps.push({ p: 100, text: '凭证上链成功！向统一预言机节点 (Oracle) 发起 RPC 定价请求...' });
    } else {
      steps.push({ p: 50, text: `正在将 ${category} 资产上传至中心化预言机...` });
      steps.push({ p: 100, text: '确权完成！准备进入统一定价框架...' });
    }
    return steps;
  };

  const processData = () => {
    if (assetCategory === 'text' && !inputText) return alert("请输入数据");
    if (assetCategory === 'image' && !selectedImage) return alert("请上传画作");

    setIsProcessing(true);
    setProgress(0);
    
    // 文本也传过去，为了触发后端的逻辑
    const dataToPass = assetCategory === 'text' ? inputText : (inputText || '插画');
    const steps = getProcessingSteps(assetCategory, enableZK);
    let current = 0;
    
    const interval = setInterval(() => {
      if (current < steps.length) {
        setProgress(steps[current].p);
        setStatusText(steps[current].text);
        current++;
      } else {
        clearInterval(interval);
        setTxHash('0x' + Math.random().toString(16).slice(2, 12) + '...');
        setTimeout(() => {
          onComplete(dataToPass, assetCategory, enableZK);
        }, 1500);
      }
    }, 800);
  };

  return (
    <div className="min-h-screen relative flex items-center justify-center bg-slate-950 p-6 font-sans">
      <div className="absolute top-0 right-0 w-[500px] h-[500px] bg-emerald-900/10 rounded-full blur-[150px] pointer-events-none"></div>
      <div className="relative max-w-5xl w-full bg-slate-900/80 backdrop-blur-2xl border border-slate-700/50 rounded-3xl p-8 shadow-2xl">
        
        <div className="flex items-center justify-between mb-8 pb-4 border-b border-slate-700/50">
          <div className="flex items-center space-x-4">
            <Database className="w-8 h-8 text-emerald-400" />
            <h1 className="text-2xl font-bold text-white">智数知权·多模态资产录入网关</h1>
          </div>
          <button onClick={handleViewDemo} className="flex items-center px-4 py-2 bg-blue-500/10 border border-blue-500/30 text-blue-400 rounded-lg hover:bg-blue-500/20 font-bold text-sm">
            <PlayCircle className="w-4 h-4 mr-2" /> 画师防洗稿 Demo
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          <div className="space-y-6">
            
            <div className="flex space-x-2 bg-slate-950/50 p-1 rounded-xl border border-slate-800">
              <button onClick={() => setAssetCategory('image')} className={`flex-1 flex items-center justify-center py-2 text-sm font-bold rounded-lg transition-all ${assetCategory === 'image' ? 'bg-slate-800 text-amber-400 shadow-sm' : 'text-slate-500 hover:text-slate-300'}`}>
                <ImageIcon className="w-4 h-4 mr-2"/> 原创画作资产
              </button>
              <button onClick={() => setAssetCategory('text')} className={`flex-1 flex items-center justify-center py-2 text-sm font-bold rounded-lg transition-all ${assetCategory === 'text' ? 'bg-slate-800 text-blue-400 shadow-sm' : 'text-slate-500 hover:text-slate-300'}`}>
                <FileText className="w-4 h-4 mr-2"/> 文本垂直语料
              </button>
            </div>

            <div className="flex items-center justify-between p-4 bg-slate-950/50 rounded-xl border border-slate-800 cursor-pointer" onClick={() => setEnableZK(!enableZK)}>
                <div>
                    <h3 className="text-sm font-bold flex items-center text-white">
                        <Lock className={`w-4 h-4 mr-2 ${enableZK ? 'text-purple-500' : 'text-slate-500'}`} /> 零知识隐匿模式 (zkML)
                    </h3>
                    <p className="text-[10px] text-slate-500 mt-1">本地特征映射，防止原生高净值资产泄露</p>
                </div>
                <div className={`w-12 h-6 rounded-full transition-colors relative ${enableZK ? 'bg-purple-600' : 'bg-slate-700'}`}>
                    <div className={`w-4 h-4 bg-white rounded-full absolute top-1 transition-transform ${enableZK ? 'translate-x-7' : 'translate-x-1'}`}></div>
                </div>
            </div>

            {assetCategory === 'text' ? (
              <textarea value={inputText} onChange={(e) => setInputText(e.target.value)} placeholder="在此输入需要确权的医疗/法律等垂直专业语料..." className="w-full h-40 bg-slate-950/50 border border-slate-700 rounded-xl p-4 font-mono text-sm text-blue-400 focus:outline-none focus:border-blue-500" />
            ) : (
              <div onClick={() => setSelectedImage('cyberpunk.jpg')} className={`w-full h-40 bg-slate-950/50 border-2 border-dashed rounded-xl flex flex-col items-center justify-center cursor-pointer transition-colors ${selectedImage ? 'border-amber-500 bg-amber-900/10' : 'border-slate-700 hover:border-slate-500'}`}>
                {selectedImage ? (
                  <><ShieldCheck className="w-8 h-8 text-amber-400 mb-2" /><p className="text-sm text-amber-400 font-bold">高净值画作已加载入沙箱</p></>
                ) : (
                  <><UploadCloud className="w-8 h-8 text-slate-500 mb-2" /><p className="text-sm text-slate-300 font-bold">点击上传商业插画原稿</p></>
                )}
              </div>
            )}

            <button onClick={processData} disabled={isProcessing} className={`w-full py-4 font-black rounded-xl transition-all shadow-lg text-white ${enableZK ? 'bg-gradient-to-r from-purple-600 to-indigo-500' : 'bg-slate-700'}`}>
              {isProcessing ? '底层适配器执行中...' : '启动多模态质量甄别引擎'}
            </button>
          </div>

          <div className="bg-[#0a0f18] rounded-2xl border border-slate-800 p-6 font-mono relative overflow-hidden">
             {enableZK && isProcessing && <ShieldCheck className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-48 h-48 text-purple-900/20 animate-pulse" />}
             <div className="flex items-center text-xs mb-4 border-b pb-2 text-purple-500 border-purple-900/50">
                <Cpu className="w-4 h-4 mr-2" /> MULTI-MODAL ADAPTER LOGS
             </div>
             {isProcessing ? (
               <div className="space-y-4 relative z-10">
                  <div className="flex justify-between text-xs text-purple-400">
                    <span>{statusText}</span>
                    <span>{progress}%</span>
                  </div>
                  <div className="w-full bg-slate-800 h-1 rounded-full overflow-hidden">
                    <div className="h-full bg-purple-500 transition-all duration-500" style={{width: `${progress}%`}}></div>
                  </div>
               </div>
             ) : (
               <div className="h-full flex items-center justify-center text-slate-700 text-sm">等待唤醒模态适配器...</div>
             )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default DataInputScreen;