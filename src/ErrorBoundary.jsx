/**
 * ErrorBoundary.jsx — React 全局错误边界
 * =========================================
 * 功能:
 *   - 捕获子树中任何未处理的 JS 运行时错误，显示友好恢复界面
 *   - 区分 ApiError（网络/超时/服务端）与未知错误，给出对应提示
 *   - 一键重试（resetErrorBoundary）+ 完整错误堆栈折叠展示（开发辅助）
 *   - 生产环境可接入外部监控（console.error 钩子位已留好）
 *
 * 用法（main.jsx 已接入）:
 *   <ErrorBoundary>
 *     <App />
 *   </ErrorBoundary>
 *
 * 也可局部包裹高风险区域:
 *   <ErrorBoundary fallbackLabel="估值面板加载失败">
 *     <OracleValuationScreen ... />
 *   </ErrorBoundary>
 */

import React from 'react';
import { AlertTriangle, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react';

// ── 错误类型识别 ─────────────────────────────────────────────────────
function classifyError(error) {
  if (!error) return { type: 'unknown', label: '未知错误' };
  const msg = (error.message || '').toLowerCase();
  if (msg.includes('network') || msg.includes('fetch') || msg.includes('failed to fetch')) {
    return { type: 'network', label: '网络连接失败', hint: '请检查后端服务是否运行，或刷新重试。' };
  }
  if (msg.includes('timeout') || msg.includes('timed out')) {
    return { type: 'timeout', label: '请求超时', hint: '服务暂时繁忙，请稍后重试。' };
  }
  if (error.name === 'ApiError' || error.type) {
    return { type: 'api', label: 'API 错误', hint: error.message || '服务端返回异常，请重试。' };
  }
  if (msg.includes('chunkloaderror') || msg.includes('loading chunk')) {
    return { type: 'chunk', label: '模块加载失败', hint: '页面需要刷新以加载最新版本。' };
  }
  return { type: 'runtime', label: '界面运行错误', hint: '发生了意外错误，可以尝试刷新页面。' };
}

// ── 错误展示组件（函数式，由 class 组件调用）─────────────────────────
function ErrorFallback({ error, resetError, fallbackLabel }) {
  const [showStack, setShowStack] = React.useState(false);
  const info = classifyError(error);
  const isDev = import.meta.env?.DEV ?? false;

  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center p-6">
      <div className="max-w-lg w-full">
        {/* 图标 + 标题 */}
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-full bg-red-900/40 border border-red-500/30 flex items-center justify-center shrink-0">
            <AlertTriangle className="w-5 h-5 text-red-400" />
          </div>
          <div>
            <p className="text-sm font-semibold text-red-300">
              {fallbackLabel || info.label}
            </p>
            <p className="text-[11px] text-slate-500 mt-0.5">
              {info.hint || '请刷新页面或联系支持。'}
            </p>
          </div>
        </div>

        {/* 操作按钮 */}
        <div className="flex gap-3 mb-4">
          <button
            onClick={resetError}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
            重试
          </button>
          <button
            onClick={() => window.location.reload()}
            className="flex items-center gap-2 px-4 py-2 rounded-lg border border-slate-600 hover:border-slate-400 text-slate-300 text-sm transition-colors"
          >
            刷新页面
          </button>
        </div>

        {/* 开发环境：折叠展示错误堆栈 */}
        {isDev && error && (
          <div className="mt-2 rounded-lg border border-slate-700 overflow-hidden">
            <button
              onClick={() => setShowStack(v => !v)}
              className="w-full flex items-center justify-between px-3 py-2 bg-slate-900 text-[11px] text-slate-400 hover:text-slate-200 transition-colors"
            >
              <span className="font-mono">{error.name}: {error.message?.slice(0, 60)}</span>
              {showStack
                ? <ChevronUp className="w-3.5 h-3.5 shrink-0" />
                : <ChevronDown className="w-3.5 h-3.5 shrink-0" />}
            </button>
            {showStack && (
              <pre className="px-3 py-2 bg-slate-950 text-[10px] text-slate-500 overflow-auto max-h-48 leading-relaxed">
                {error.stack}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Class-based ErrorBoundary（React 要求 class 组件实现 getDerivedStateFromError）
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
    this.resetError = this.resetError.bind(this);
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    // 生产环境可在此接入 Sentry / Datadog RUM / 自建上报
    console.error('[ErrorBoundary] 捕获到未处理错误:', error, info?.componentStack);
  }

  resetError() {
    this.setState({ hasError: false, error: null });
    this.props.onReset?.();
  }

  render() {
    if (this.state.hasError) {
      return (
        <ErrorFallback
          error={this.state.error}
          resetError={this.resetError}
          fallbackLabel={this.props.fallbackLabel}
        />
      );
    }
    return this.props.children;
  }
}
