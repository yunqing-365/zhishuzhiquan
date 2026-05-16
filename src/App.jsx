import React, { useState } from 'react';
import DataInputScreen from './DataInputScreen';
import OracleValuationScreen from './OracleValuationScreen';
import SmartSplitScreen from './SmartSplitScreen';

function App() {
  const [currentStep, setCurrentStep]     = useState(1);
  const [assetData, setAssetData]         = useState('');
  const [assetCategory, setAssetCategory] = useState('text');
  const [isZkMode, setIsZkMode]           = useState(true);
  const [sceneOverride, setSceneOverride] = useState(null); // ★ v3: 场景覆盖调试参数

  const handleRestart = () => {
    setCurrentStep(1);
    setAssetData('');
    setAssetCategory('text');
    setSceneOverride(null); // 重置时清除覆盖
  };

  return (
    <div className="font-sans antialiased text-slate-200 selection:bg-teal-500/30">
      {currentStep === 1 && (
        <DataInputScreen
          onComplete={(data, category, zkEnabled, override) => {
            setAssetData(data);
            setAssetCategory(category);
            setIsZkMode(zkEnabled);
            setSceneOverride(override ?? null); // ★ 接收第 4 参数
            setCurrentStep(2);
          }}
        />
      )}

      {currentStep === 2 && (
        <OracleValuationScreen
          assetData={assetData}
          assetCategory={assetCategory}
          isZkMode={isZkMode}
          sceneOverride={sceneOverride} // ★ 透传给预言机
          onNext={() => setCurrentStep(3)}
        />
      )}

      {currentStep === 3 && (
        <SmartSplitScreen onRestart={handleRestart} />
      )}
    </div>
  );
}

export default App;
