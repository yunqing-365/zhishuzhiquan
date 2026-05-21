import React, { useState, useCallback } from 'react';
import { WagmiProvider }          from 'wagmi';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RainbowKitProvider, darkTheme }    from '@rainbow-me/rainbowkit';
import '@rainbow-me/rainbowkit/styles.css';

import { History, BarChart2 }     from 'lucide-react';
import DataInputScreen            from './DataInputScreen';
import OracleValuationScreen      from './OracleValuationScreen';
import SmartSplitScreen           from './SmartSplitScreen';
import HistoryPanel               from './HistoryPanel';
import AnalyticsDashboard         from './AnalyticsDashboard';
import WalletButton               from './web3/WalletButton';
import { wagmiConfig, targetChain } from './web3/config';
import { useApiHealth }           from './api';

// ── React Query Client（wagmi v2 依赖）────────────────────────────
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30_000 } },
});

// ─── 步骤元数据 ────────────────────────────────────────────────────
const STEPS = [
  { id: 1, label: '资产录入',   short: '录入' },
  { id: 2, label: '预言机估值', short: '估值' },
  { id: 3, label: '合约分账',   short: '分账' },
];

// ─── 进度条组件 ────────────────────────────────────────────────────
// ── 后端心跳状态指示器 ────────────────────────────────────────────
const BackendStatus = () => {
  const { status, version, corpusSize } = useApiHealth();
  const cfg = {
    checking: { dot: 'bg-slate-500 animate-pulse', text: 'text-slate-500', label: '连接中...' },
    online:   { dot: 'bg-emerald-500',             text: 'text-emerald-400', label: `后端 ${version || 'v?'} · 库${corpusSize ?? '?'}条` },
    offline:  { dot: 'bg-red-500',                 text: 'text-red-400',    label: 'Mock 模式' },
  }[status];
  return (
    <div className={`shrink-0 flex items-center gap-1.5 text-[10px] font-mono ${cfg.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
      <span className="hidden md:inline">{cfg.label}</span>
    </div>
  );
};

const ProgressBar = ({ step, category, onBack, canGoBack, onHistory, onAnalytics }) => {
  const modeColor = {
    audio: 'bg-emerald-500',
    image: 'bg-amber-500',
    text:  'bg-purple-500',
    video: 'bg-violet-500',
  }[category] || 'bg-purple-500';

  return (
    <div className="fixed top-0 left-0 right-0 z-50 bg-slate-950/90 backdrop-blur-xl border-b border-slate-800/60 px-4 py-2 flex items-center gap-3">
      {canGoBack && (
        <button
          onClick={onBack}
          className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700 text-slate-400 hover:text-slate-200 hover:border-slate-500 text-xs font-mono transition-all"
        >
          ← 返回
        </button>
      )}

      <div className="flex items-center gap-1 shrink-0">
        {STEPS.map((s, i) => (
          <React.Fragment key={s.id}>
            <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-bold transition-all ${
              s.id === step
                ? 'bg-slate-800 text-white border border-slate-600'
                : s.id < step ? 'text-slate-500' : 'text-slate-700'
            }`}>
              <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-black ${
                s.id < step   ? 'bg-emerald-600 text-white' :
                s.id === step ? 'bg-slate-600 text-white'   :
                                'bg-slate-800 text-slate-600'
              }`}>
                {s.id < step ? '✓' : s.id}
              </span>
              <span className="hidden sm:inline">{s.label}</span>
              <span className="sm:hidden">{s.short}</span>
            </div>
            {i < STEPS.length - 1 && (
              <div className={`w-5 h-px transition-colors ${s.id < step ? 'bg-emerald-700' : 'bg-slate-800'}`} />
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

      {/* 历史 */}
      <button
        onClick={onHistory}
        className="shrink-0 flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-slate-700 text-slate-400 hover:text-slate-200 hover:border-purple-500/50 text-xs font-mono transition-all"
      >
        <History className="w-3.5 h-3.5" />
        <span className="hidden sm:inline">历史</span>
      </button>

      {/* ★ 看板 */}
      <button
        onClick={onAnalytics}
        className="shrink-0 flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-slate-700 text-slate-400 hover:text-slate-200 hover:border-purple-500/50 text-xs font-mono transition-all"
      >
        <BarChart2 className="w-3.5 h-3.5" />
        <span className="hidden sm:inline">看板</span>
      </button>

      {/* 模态标记 */}
      <div className={`shrink-0 text-[10px] font-mono px-2 py-1 rounded border ${
        category === 'audio' ? 'text-emerald-400 border-emerald-500/30 bg-emerald-900/20' :
        category === 'image' ? 'text-amber-400  border-amber-500/30  bg-amber-900/20'    :
        category === 'video' ? 'text-violet-400 border-violet-500/30 bg-violet-900/20'   :
                               'text-blue-400   border-blue-500/30   bg-blue-900/20'
      }`}>
        {category || 'text'}
      </div>

      {/* ★ 后端心跳状态 */}
      <BackendStatus />

      {/* ★ 钱包连接按钮（步骤3时高亮） */}
      <div className="shrink-0">
        <WalletButton size="sm" showChain={step === 3} />
      </div>
    </div>
  );
};

// ─── 主 App（内层，已在 Provider 内）──────────────────────────────
function AppInner() {
  const [currentStep, setCurrentStep]       = useState(1);
  const [assetData, setAssetData]           = useState('');
  const [assetCategory, setAssetCategory]   = useState('text');
  const [isZkMode, setIsZkMode]             = useState(true);
  const [sceneOverride, setSceneOverride]   = useState(null);
  const [audioData, setAudioData]           = useState(null);
  const [imageData, setImageData]           = useState(null);
  const [videoData, setVideoData]           = useState(null);
  const [valuationResult, setValuationResult] = useState(null);
  const [showHistory, setShowHistory]       = useState(false);
  const [showAnalytics, setShowAnalytics]   = useState(false);
  const [stepHistory, setStepHistory]       = useState([]);

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
    setImageData(null);
    setVideoData(null);
    setValuationResult(null);
  }, []);

  const handleInputComplete = useCallback((data, category, zkEnabled, override, audioB64, imgB64, videoB64) => {
    setAssetData(data);
    setAssetCategory(category);
    setIsZkMode(zkEnabled);
    setSceneOverride(override ?? null);
    setAudioData(audioB64 ?? null);
    setImageData(imgB64 ?? null);
    setVideoData(videoB64 ?? null);
    goTo(2);
  }, [goTo]);

  const handleValuationNext = useCallback((result) => {
    setValuationResult(result);
    goTo(3);
  }, [goTo]);

  const canGoBack = stepHistory.length > 0 && currentStep > 1;

  return (
    <div className="font-sans antialiased text-slate-200 selection:bg-teal-500/30">
      {currentStep > 1 && (
        <ProgressBar
          step={currentStep}
          category={assetCategory}
          onBack={goBack}
          canGoBack={canGoBack}
          onHistory={() => setShowHistory(true)}
          onAnalytics={() => setShowAnalytics(true)}
        />
      )}

      <div className={currentStep > 1 ? 'pt-14' : ''}>
        {currentStep === 1 && (
          <DataInputScreen
            onComplete={handleInputComplete}
            onHistory={() => setShowHistory(true)}
          />
        )}
        {currentStep === 2 && (
          <OracleValuationScreen
            assetData={assetData}
            assetCategory={assetCategory}
            isZkMode={isZkMode}
            sceneOverride={sceneOverride}
            audioData={audioData}
            imageData={imageData}
            videoData={videoData}
            onNext={handleValuationNext}
          />
        )}
        {currentStep === 3 && (
          <SmartSplitScreen
            valuationResult={valuationResult}
            assetCategory={assetCategory}
            onRestart={handleRestart}
            onBack={goBack}
          />
        )}
      </div>

      <HistoryPanel isOpen={showHistory} onClose={() => setShowHistory(false)} />
      <AnalyticsDashboard isOpen={showAnalytics} onClose={() => setShowAnalytics(false)} />
    </div>
  );
}

// ─── 根组件：注入所有 Provider ─────────────────────────────────────
export default function App() {
  return (
    <WagmiProvider config={wagmiConfig}>
      <QueryClientProvider client={queryClient}>
        <RainbowKitProvider
          theme={darkTheme({
            accentColor:          '#8b5cf6',
            accentColorForeground: '#ffffff',
            borderRadius:         'large',
            fontStack:            'system',
          })}
          locale="zh-CN"
          initialChain={targetChain}
        >
          <AppInner />
        </RainbowKitProvider>
      </QueryClientProvider>
    </WagmiProvider>
  );
}
