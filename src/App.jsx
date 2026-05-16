import React, { useState } from 'react';
import DataInputScreen from './DataInputScreen';
import OracleValuationScreen from './OracleValuationScreen';
import SmartSplitScreen from './SmartSplitScreen';

function App() {
  const [currentStep, setCurrentStep]         = useState(1);
  const [assetData, setAssetData]             = useState('');
  const [assetCategory, setAssetCategory]     = useState('text');
  const [isZkMode, setIsZkMode]               = useState(true);
  const [sceneOverride, setSceneOverride]     = useState(null);
  const [audioData, setAudioData]             = useState(null);   // ★ v4: audio base64
  const [valuationResult, setValuationResult] = useState(null);   // ★ v4: oracle 真实结果透传

  const handleRestart = () => {
    setCurrentStep(1);
    setAssetData('');
    setAssetCategory('text');
    setSceneOverride(null);
    setAudioData(null);
    setValuationResult(null);
  };

  return (
    <div className="font-sans antialiased text-slate-200 selection:bg-teal-500/30">
      {currentStep === 1 && (
        <DataInputScreen
          onComplete={(data, category, zkEnabled, override, audioB64) => {
            setAssetData(data);
            setAssetCategory(category);
            setIsZkMode(zkEnabled);
            setSceneOverride(override ?? null);
            setAudioData(audioB64 ?? null);   // ★ v4: 接收第 5 参数
            setCurrentStep(2);
          }}
        />
      )}

      {currentStep === 2 && (
        <OracleValuationScreen
          assetData={assetData}
          assetCategory={assetCategory}
          isZkMode={isZkMode}
          sceneOverride={sceneOverride}
          audioData={audioData}              // ★ v4: 传入音频数据
          onNext={(result) => {             // ★ v4: 接收 oracle 完整结果
            setValuationResult(result);
            setCurrentStep(3);
          }}
        />
      )}

      {currentStep === 3 && (
        <SmartSplitScreen
          valuationResult={valuationResult}  // ★ v4: 传入真实定价结果
          assetCategory={assetCategory}
          onRestart={handleRestart}
        />
      )}
    </div>
  );
}

export default App;
