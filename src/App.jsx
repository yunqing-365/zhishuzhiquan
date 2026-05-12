import React, { useState } from 'react';
import DataInputScreen from './DataInputScreen';
import OracleValuationScreen from './OracleValuationScreen';
import SmartSplitScreen from './SmartSplitScreen';

function App() {
  const [currentStep, setCurrentStep] = useState(1);
  const [assetData, setAssetData] = useState(""); 
  const [assetCategory, setAssetCategory] = useState("text"); // 新增：多模态数据类别
  const [isZkMode, setIsZkMode] = useState(true);

  const handleRestart = () => {
    setCurrentStep(1);
    setAssetData(""); 
    setAssetCategory("text");
  };

  return (
    <div className="font-sans antialiased text-slate-200 selection:bg-teal-500/30">
      {currentStep === 1 && (
        <DataInputScreen onComplete={(data, category, zkEnabled) => {
          setAssetData(data); 
          setAssetCategory(category); // 接收并保存多模态类别
          setIsZkMode(zkEnabled);
          setCurrentStep(2);
        }} />
      )}
      
      {currentStep === 2 && (
        <OracleValuationScreen 
          assetData={assetData} 
          assetCategory={assetCategory} // 透传给预言机
          isZkMode={isZkMode} 
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