import React, { useState } from 'react';
import { UploadCloud, Cpu, Database, PlayCircle, ShieldCheck, Lock, Image as ImageIcon, FileText, Tag } from 'lucide-react';

// 场景预设 Demo — 覆盖不同价值区间
const DEMO_PRESETS = [
  {
    label: '赛博朋克插画 (高价值)',
    category: 'image',
    description: '赛博朋克风格原创插画，机甲少女，精细光影构图，4k手绘数字绘画，CG艺术，蒸汽朋克风格细节，专业原画水准',
  },
  {
    label: '医疗 SFT 语料',
    category: 'text',
    description: '患者男，45岁，诊断为2型糖尿病，血糖14.2mmol/L，医嘱：二甲双胍0.5g tid，监测血压、心率，禁忌症：肝肾功能不全，定期复查糖化血红蛋白。',
  },
  {
    label: '法律合同文本',
    category: 'text',
    description: '本合同由甲方（委托方）与乙方（受托方）依据相关法律法规签订。第三条：如有违约，须承担仲裁责任并赔偿损失。第四条：保密义务期限为合同终止后三年，涉及知识产权归属条款见附件。',
  },
  {
    label: '废话文学 (熔断)',
    category: 'text',
    description: '就是说那个吧就是真的真的就是说嗯嗯那个那个感觉吧感觉就是就是那个',
  },
  {
    label: '截图素材 (低价值)',
    category: 'image',
    description: '浏览器截图，Chrome界面截屏，普通桌面UI截图',
  },
];

const DataInputScreen = ({ onComplete }) => {
  const [isProcessing, setIsProcessing] = useState(false);
  const [progress, setProgress] = useState(0);
  const [statusText, setStatusText] = useState('');
  const [enableZK, setEnableZK] = useState(true);
  const [assetCategory, setAssetCategory] = useState('image');
  const [inputText, setInputText] = useState('');          // 文本内容 / 图像描述
  const [selectedImage, setSelectedImage] = useState(null);
  const [activePreset, setActivePreset] = useState(null);

  // 图像模式下，描述文字是传给后端 Scene Classifier 的核心输入
  const getDescriptionForBackend = () => {
    if (assetCategory === 'text') return inputText;
    // 图像: 用户填写的描述 > demo preset > 空描述 fallback
    return inputText || '原创图像';
  };

  const handlePreset = (preset) => {
    setActivePreset(preset.label);
    setAssetCategory(preset.category);
    setInputText(preset.description);
    if (preset.category === 'image') setSelectedImage('preset_image.png');
    else setSelectedImage(null);
  };

  const getProcessingSteps = (category, isZk, description) => {
    const steps = [];
    if (isZk) {
      steps.push({ p: 10, text: '【Stage 1 — 模态路由】初始化 WebAssembly 沙箱，识别模态类型...' });
      steps.push({ p: 28, text: `【Stage 2 — 场景分类】SceneClassifier 分析 ${category === 'image' ? '图像描述' : '文本领域'}，识别子场景...' });
      if (category === 'image') {
        steps.push({ p: 45, text: '【Stage 3 — 特征提取】ImageAdapter: LAION-Aesthetics 美学评估 + DWT 隐写鲁棒性...' });
        steps.push({ p: 62, text: '【Stage 3 — 稀缺度】CLIP 512维特征对齐，计算画派风格稀缺度...' });
      } else {
        steps.push({ p: 45, text: '【Stage 3 — 特征提取】TextAdapter: 场景专项 SNR + Shannon 熵 + 废话熔断检测...' });
        steps.push({ p: 62, text: '【Stage 3 — 图谱构建】GraphRAG 实体拓扑密度 + KNN-Shapley 边际贡献评估...' });
      }
      steps.push({ p: 78, text: '【Stage 4 — TEV 标准化】双层乘数 (模态权重 × 场景子权重) 映射至统一定价框架...' });
      steps.push({ p: 92, text: '【zk-SNARK】生成零知识证明凭证，本地明文安全销毁...' });
      steps.push({ p: 100, text: '凭证上链成功！向预言机节点发起 RPC 定价请求...' });
    } else {
      steps.push({ p: 50, text: `正在将 ${category} 资产上传至中心化预言机...` });
      steps.push({ p: 100, text: '确权完成！准备进入统一定价框架...' });
    }
    return steps;
  };

  const processData = () => {
    const desc = getDescriptionForBackend();
    if (assetCategory === 'text' && !desc.trim()) return alert('请输入文本内容');
    if (assetCategory === 'image' && !selectedImage) return alert('请上传画作或选择 Demo 预设');

    setIsProcessing(true);
    setProgress(0);

    const steps = getProcessingSteps(assetCategory, enableZK, desc);
    let cur = 0;
    const interval = setInterval(() => {
      if (cur < steps.length) {
        setProgress(steps[cur].p);
        setStatusText(steps[cur].text);
        cur++;
      } else {
        clearInterval(interval);
        setTimeout(() => onComplete(desc, assetCategory, enableZK), 1200);
      }
    }, 750);
  };

  return (
    <div className="min-h-screen relative flex items-center justify-center bg-slate-950 p-6 font-sans">
      <div className="absolute top-0 right-0 w-[500px] h-[500px] bg-emerald-900/10 rounded-full blur-[150px] pointer-events-none" />
      <div className="relative max-w-5xl w-full bg-slate-900/80 backdrop-blur-2xl border border-slate-700/50 rounded-3xl p-8 shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between mb-6 pb-4 border-b border-slate-700/50">
          <div className="flex items-center space-x-3">
            <Database className="w-7 h-7 text-emerald-400" />
            <h1 className="text-xl font-bold text-white">智数知权 · 多模态资产录入网关</h1>
          </div>
          <div className="flex items-center gap-2 text-xs text-slate-500 font-mono">
            <Tag className="w-3.5 h-3.5" />
            <span>Scene Classifier v2.1</span>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">

          {/* LEFT: input area */}
          <div className="space-y-4">

            {/* 模态切换 */}
            <div className="flex space-x-2 bg-slate-950/50 p-1 rounded-xl border border-slate-800">
              <button
                onClick={() => { setAssetCategory('image'); setInputText(''); setSelectedImage(null); }}
                className={`flex-1 flex items-center justify-center py-2 text-sm font-bold rounded-lg transition-all ${assetCategory === 'image' ? 'bg-slate-800 text-amber-400 shadow-sm' : 'text-slate-500 hover:text-slate-300'}`}
              >
                <ImageIcon className="w-4 h-4 mr-2" /> 原创画作资产
              </button>
              <button
                onClick={() => { setAssetCategory('text'); setSelectedImage(null); setInputText(''); }}
                className={`flex-1 flex items-center justify-center py-2 text-sm font-bold rounded-lg transition-all ${assetCategory === 'text' ? 'bg-slate-800 text-blue-400 shadow-sm' : 'text-slate-500 hover:text-slate-300'}`}
              >
                <FileText className="w-4 h-4 mr-2" /> 文本垂直语料
              </button>
            </div>

            {/* zkML 开关 */}
            <div
              className="flex items-center justify-between p-3.5 bg-slate-950/50 rounded-xl border border-slate-800 cursor-pointer"
              onClick={() => setEnableZK(!enableZK)}
            >
              <div>
                <h3 className="text-sm font-bold flex items-center text-white">
                  <Lock className={`w-4 h-4 mr-2 ${enableZK ? 'text-purple-500' : 'text-slate-500'}`} />
                  零知识隐匿模式 (zkML)
                </h3>
                <p className="text-[10px] text-slate-500 mt-0.5">本地特征映射，防止高净值资产原文泄露</p>
              </div>
              <div className={`w-12 h-6 rounded-full transition-colors relative ${enableZK ? 'bg-purple-600' : 'bg-slate-700'}`}>
                <div className={`w-4 h-4 bg-white rounded-full absolute top-1 transition-transform ${enableZK ? 'translate-x-7' : 'translate-x-1'}`} />
              </div>
            </div>

            {/* 文本模式: 直接输入 */}
            {assetCategory === 'text' && (
              <textarea
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                placeholder="输入需要确权的语料 (医疗 / 法律 / 代码 / 创意写作 / 问答对话)...
Scene Classifier 将自动识别领域场景并调整定价权重。"
                className="w-full h-36 bg-slate-950/50 border border-slate-700 rounded-xl p-4 font-mono text-sm text-blue-400 placeholder-slate-600 focus:outline-none focus:border-blue-500 resize-none"
              />
            )}

            {/* 图像模式: 上传区 + 描述输入 */}
            {assetCategory === 'image' && (
              <div className="space-y-3">
                <div
                  onClick={() => setSelectedImage('user_artwork.png')}
                  className={`w-full h-24 bg-slate-950/50 border-2 border-dashed rounded-xl flex flex-col items-center justify-center cursor-pointer transition-colors ${selectedImage ? 'border-amber-500 bg-amber-900/10' : 'border-slate-700 hover:border-slate-500'}`}
                >
                  {selectedImage ? (
                    <><ShieldCheck className="w-6 h-6 text-amber-400 mb-1" /><p className="text-xs text-amber-400 font-bold">画作已加载入沙箱</p></>
                  ) : (
                    <><UploadCloud className="w-6 h-6 text-slate-500 mb-1" /><p className="text-xs text-slate-300 font-bold">点击上传商业插画原稿</p></>
                  )}
                </div>
                {/* ★ 新增: 画作描述输入 — Scene Classifier 的核心输入 */}
                <div>
                  <p className="text-[10px] text-slate-500 mb-1.5 flex items-center gap-1">
                    <Tag className="w-3 h-3" />
                    描述画作场景/风格 <span className="text-purple-400">(Scene Classifier 依赖此字段)</span>
                  </p>
                  <textarea
                    value={inputText}
                    onChange={(e) => setInputText(e.target.value)}
                    placeholder="例: 赛博朋克风格原创插画，机甲少女，精细光影，4k手绘，原画师作品
或: 普通照片 / 浏览器截图 / 技术架构图..."
                    className="w-full h-20 bg-slate-950/50 border border-slate-700 rounded-xl p-3 font-mono text-xs text-amber-400 placeholder-slate-600 focus:outline-none focus:border-amber-500 resize-none"
                  />
                </div>
              </div>
            )}

            <button
              onClick={processData}
              disabled={isProcessing}
              className={`w-full py-3.5 font-black rounded-xl transition-all shadow-lg text-white text-sm ${enableZK ? 'bg-gradient-to-r from-purple-600 to-indigo-500 hover:from-purple-700 hover:to-indigo-600' : 'bg-slate-700 hover:bg-slate-600'} ${isProcessing ? 'opacity-60 cursor-not-allowed' : ''}`}
            >
              {isProcessing ? '底层适配器执行中...' : '启动多模态质量甄别引擎'}
            </button>
          </div>

          {/* RIGHT: Demo presets + log */}
          <div className="space-y-4">
            {/* Demo 预设区 */}
            <div>
              <p className="text-[10px] text-slate-500 uppercase tracking-widest mb-2 flex items-center gap-1.5">
                <PlayCircle className="w-3 h-3" /> 快速 Demo 预设
              </p>
              <div className="space-y-1.5">
                {DEMO_PRESETS.map((preset) => (
                  <button
                    key={preset.label}
                    onClick={() => handlePreset(preset)}
                    className={`w-full text-left px-3 py-2.5 rounded-lg border text-xs font-mono transition-all ${
                      activePreset === preset.label
                        ? 'bg-purple-900/30 border-purple-500/50 text-purple-300'
                        : 'bg-slate-950/40 border-slate-800 text-slate-400 hover:border-slate-600 hover:text-slate-300'
                    }`}
                  >
                    <span className="font-bold">{preset.label}</span>
                    <span className={`ml-2 text-[10px] ${preset.category === 'image' ? 'text-amber-500' : 'text-blue-500'}`}>
                      [{preset.category}]
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {/* 执行日志 */}
            <div className="bg-[#0a0f18] rounded-xl border border-slate-800 p-4 font-mono flex-1">
              <div className="flex items-center text-[10px] mb-3 border-b pb-2 text-purple-500 border-purple-900/50">
                <Cpu className="w-3.5 h-3.5 mr-2" /> ADAPTER EXECUTION LOG
              </div>
              {isProcessing ? (
                <div className="space-y-2">
                  <div className="flex justify-between text-[10px] text-purple-400">
                    <span className="flex-1 leading-tight">{statusText}</span>
                    <span className="ml-2 shrink-0">{progress}%</span>
                  </div>
                  <div className="w-full bg-slate-800 h-1 rounded-full overflow-hidden">
                    <div className="h-full bg-purple-500 transition-all duration-500" style={{ width: `${progress}%` }} />
                  </div>
                </div>
              ) : (
                <div className="text-slate-700 text-[11px] text-center pt-4">
                  选择预设或输入资产，启动引擎...
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DataInputScreen;
