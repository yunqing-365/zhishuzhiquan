import React, { useState, useCallback } from 'react';
import DataInputScreen        from './DataInputScreen';
import OracleValuationScreen  from './OracleValuationScreen';
import SmartSplitScreen       from './SmartSplitScreen';

// ─── 步骤元数据 ────────────────────────────────────────────────────
const STEPS = [
  { id: 1, label: '资产录入',   short: '录入' },
  { id: 2, label: '预言机估值', short: '估值' },
  { id: 3, label: '合约分账',   short: '分账' },
];

// ─── 进度条组件 ────────────────────────────────────────────────────
const ProgressBar = ({ step, category, onBack, canGoBack }) => {
  const modeColor = {
    audio: 'bg-emerald-500',
    image: 'bg-amber-500',
    text:  'bg-purple-500',
  }[category] || 'bg-purple-500';

  return (
    <div className="fixed top-0 left-0 right-0 z-50 bg-slate-950/90 backdrop-blur-xl border-b border-slate-800/60 px-6 py-2.5 flex items-center gap-4">
      {/* 返回按钮 */}
      {canGoBack && (
        <button
          onClick={onBack}
          className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700 text-slate-400 hover:text-slate-200 hover:border-slate-500 text-xs font-mono transition-all"
        >
          ← 返回
        </button>
      )}

      {/* 步骤指示器 */}
      <div className="flex items-center gap-1 shrink-0">
        {STEPS.map((s, i) => (
          <React.Fragment key={s.id}>
            <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-bold transition-all ${
              s.id === step
                ? 'bg-slate-800 text-white border border-slate-600'
                : s.id < step
                ? 'text-slate-500'
                : 'text-slate-700'
            }`}>
              <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-black ${
                s.id < step  ? 'bg-emerald-600 text-white' :
                s.id === step ? 'bg-slate-600 text-white' :
                'bg-slate-800 text-slate-600'
              }`}>
                {s.id < step ? '✓' : s.id}
              </span>
              <span className="hidden sm:inline">{s.label}</span>
              <span className="sm:hidden">{s.short}</span>
            </div>
            {i < STEPS.length - 1 && (
              <div className={`w-6 h-px transition-colors ${s.id < step ? 'bg-emerald-700' : 'bg-slate-800'}`} />
            )}
          </React.Fragment>
        ))}
      </div>

      {/* 进度线 */}
      <div className="flex-1 h-1 bg-slate-800 rounded-full overflow-hidden">
        <div
          className={`h-full ${modeColor} transition-all duration-700 ease-out`}
          style={{ width: `${((step - 1) / (STEPS.length - 1)) * 100}%` }}
        />
      </div>

      {/* 模态标记 */}
      <div className={`shrink-0 text-[10px] font-mono px-2 py-1 rounded border ${
        category === 'audio' ? 'text-emerald-400 border-emerald-500/30 bg-emerald-900/20' :
        category === 'image' ? 'text-amber-400  border-amber-500/30  bg-amber-900/20'   :
                               'text-blue-400   border-blue-500/30   bg-blue-900/20'
      }`}>
        {category || 'text'}
      </div>
    </div>
  );
};

// ─── 主 App ────────────────────────────────────────────────────────
function App() {
  const [currentStep, setCurrentStep]       = useState(1);
  const [assetData, setAssetData]           = useState('');
  const [assetCategory, setAssetCategory]   = useState('text');
  const [isZkMode, setIsZkMode]             = useState(true);
  const [sceneOverride, setSceneOverride]   = useState(null);
  const [audioData, setAudioData]           = useState(null);
  const [valuationResult, setValuationResult] = useState(null);

  // ★ 新增：历史栈，支持返回时恢复状态
  const [stepHistory, setStepHistory] = useState([]);

  const goTo = useCallback((nextStep) => {
    setStepHistory(h => [...h, currentStep]);
    setCurrentStep(nextStep);
  }, [currentStep]);

  const goBack = useCallback(() => {
    const prev = stepHistory[stepHistory.length - 1];
    if (prev == null) return;
    setStepHistory(h => h.slice(0, -1));
    setCurrentStep(prev);
  }, [stepHistory]);

  const handleRestart = useCallback(() => {
    setCurrentStep(1);
    setStepHistory([]);
    setAssetData('');
    setAssetCategory('text');
    setIsZkMode(true);
    setSceneOverride(null);
    setAudioData(null);
    setValuationResult(null);
  }, []);

  // 步骤 1 → 2
  const handleInputComplete = useCallback((data, category, zkEnabled, override, audioB64) => {
    setAssetData(data);
    setAssetCategory(category);
    setIsZkMode(zkEnabled);
    setSceneOverride(override ?? null);
    setAudioData(audioB64 ?? null);
    goTo(2);
  }, [goTo]);

  // 步骤 2 → 3
  const handleValuationNext = useCallback((result) => {
    setValuationResult(result);
    goTo(3);
  }, [goTo]);

  // 步骤 3：返回步骤 2（不需要 goBack，直接跳，保留当前 valuationResult）
  const handleBackToValuation = useCallback(() => {
    goBack();
  }, [goBack]);

  const canGoBack = stepHistory.length > 0 && currentStep > 1;

  return (
    <div className="font-sans antialiased text-slate-200 selection:bg-teal-500/30">
      {/* 顶部全局进度条（步骤 2、3 显示；步骤 1 为首屏不显示，避免干扰） */}
      {currentStep > 1 && (
        <ProgressBar
          step={currentStep}
          category={assetCategory}
          onBack={goBack}
          canGoBack={canGoBack}
        />
      )}

      {/* 步骤 2、3 时内容区域下移，避免被进度条遮挡 */}
      <div className={currentStep > 1 ? 'pt-12' : ''}>
        {currentStep === 1 && (
          <DataInputScreen onComplete={handleInputComplete} />
        )}

        {currentStep === 2 && (
          <OracleValuationScreen
            assetData={assetData}
            assetCategory={assetCategory}
            isZkMode={isZkMode}
            sceneOverride={sceneOverride}
            audioData={audioData}
            onNext={handleValuationNext}
          />
        )}

        {currentStep === 3 && (
          <SmartSplitScreen
            valuationResult={valuationResult}
            assetCategory={assetCategory}
            onRestart={handleRestart}
            onBack={handleBackToValuation}   // ★ 新增：传给 SmartSplitScreen
          />
        )}
      </div>
    </div>
  );
}

export default App;
