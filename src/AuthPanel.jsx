// src/AuthPanel.jsx — 登录 / 注册面板
/**
 * 知数知圈 · 身份认证 UI
 * 支持：注册 · 登录 · 展示当前登录状态 · 退出
 * 由 App.jsx 顶栏触发，完成后派发 CustomEvent 供上层刷新状态
 */
import React, { useState, useEffect, useRef } from 'react';
import {
  X, User, Lock, Mail, Tag, Eye, EyeOff,
  LogIn, UserPlus, LogOut, CheckCircle2, AlertCircle, Loader2,
} from 'lucide-react';
import { authClient, tokenStore } from './api';

// ════════════════════════════════════════════════════════════════════
// 子组件
// ════════════════════════════════════════════════════════════════════

function Field({ icon: Icon, type = 'text', placeholder, value, onChange, right }) {
  return (
    <div className="relative">
      <Icon size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
      <input
        type={type}
        placeholder={placeholder}
        value={value}
        onChange={e => onChange(e.target.value)}
        className="w-full bg-slate-900 border border-slate-700 rounded-lg pl-9 pr-10 py-2.5
                   text-sm text-slate-200 placeholder-slate-600 focus:outline-none
                   focus:border-cyan-500/60 focus:ring-1 focus:ring-cyan-500/20 transition-all"
        autoComplete="off"
      />
      {right && <div className="absolute right-3 top-1/2 -translate-y-1/2">{right}</div>}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// 主组件
// ════════════════════════════════════════════════════════════════════

export default function AuthPanel({ isOpen, onClose, onAuthChange }) {
  const [mode, setMode]         = useState('login');    // login | register
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail]       = useState('');
  const [showPwd, setShowPwd]   = useState(false);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState('');
  const [success, setSuccess]   = useState('');
  const [creator, setCreator]   = useState(null);
  const inputRef = useRef(null);

  // 每次打开时刷新登录状态
  useEffect(() => {
    if (isOpen) {
      setCreator(tokenStore.getCreator());
      setError(''); setSuccess('');
      setTimeout(() => inputRef.current?.focus(), 150);
    }
  }, [isOpen]);

  const reset = () => {
    setUsername(''); setPassword(''); setDisplayName(''); setEmail('');
    setError(''); setSuccess('');
  };

  const switchMode = (m) => { setMode(m); reset(); };

  const handleSubmit = async () => {
    if (!username.trim() || !password.trim()) {
      setError('用户名和密码不能为空');
      return;
    }
    setLoading(true); setError(''); setSuccess('');
    try {
      if (mode === 'login') {
        const data = await authClient.login({ username: username.trim(), password });
        setSuccess(`欢迎回来，${data.display_name || data.username}！`);
        setCreator({ creator_id: data.creator_id, username: data.username, display_name: data.display_name });
        onAuthChange?.('login', data);
        setTimeout(onClose, 1200);
      } else {
        if (password.length < 6) { setError('密码至少 6 位'); setLoading(false); return; }
        const data = await authClient.register({
          username: username.trim(),
          password,
          display_name: displayName.trim() || username.trim(),
          email: email.trim(),
        });
        setSuccess(`注册成功！欢迎，${data.display_name}！`);
        setCreator({ creator_id: data.creator_id, username: data.username, display_name: data.display_name });
        onAuthChange?.('register', data);
        setTimeout(onClose, 1200);
      }
    } catch (e) {
      setError(e.message || '操作失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = () => {
    authClient.logout();
    setCreator(null);
    onAuthChange?.('logout', null);
    onClose();
  };

  const onKeyDown = (e) => { if (e.key === 'Enter') handleSubmit(); };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="relative w-full max-w-sm bg-slate-950 border border-slate-800 rounded-2xl shadow-2xl overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* 顶部装饰条 */}
        <div className="h-1 w-full bg-gradient-to-r from-cyan-500 via-blue-500 to-purple-500" />

        {/* 关闭按钮 */}
        <button
          onClick={onClose}
          className="absolute top-3 right-3 p-1.5 rounded-lg hover:bg-slate-800 text-slate-500 hover:text-white transition-colors"
        >
          <X size={16} />
        </button>

        <div className="p-6">

          {/* ── 已登录状态 ──────────────────────────────────────── */}
          {creator ? (
            <div className="space-y-5">
              <div className="text-center space-y-2">
                <div className="w-14 h-14 rounded-full bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center text-white text-2xl font-bold mx-auto">
                  {(creator.display_name || creator.username || '?')[0].toUpperCase()}
                </div>
                <div>
                  <p className="text-white font-semibold">{creator.display_name || creator.username}</p>
                  <p className="text-slate-500 text-xs font-mono mt-0.5">{creator.creator_id?.slice(0, 20)}…</p>
                </div>
              </div>
              <div className="rounded-xl bg-emerald-950/40 border border-emerald-800/40 p-3 text-center">
                <p className="text-emerald-400 text-sm flex items-center justify-center gap-2">
                  <CheckCircle2 size={14} /> 已登录
                </p>
              </div>
              <button
                onClick={handleLogout}
                className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl border border-red-800/60
                           text-red-400 hover:bg-red-950/30 hover:text-red-300 text-sm font-medium transition-all"
              >
                <LogOut size={15} /> 退出登录
              </button>
            </div>

          ) : (
            /* ── 登录/注册表单 ─────────────────────────────────── */
            <div className="space-y-4">
              {/* Tab */}
              <div className="flex rounded-xl bg-slate-900 p-1">
                {['login', 'register'].map((m) => (
                  <button
                    key={m}
                    onClick={() => switchMode(m)}
                    className={`flex-1 py-2 rounded-lg text-sm font-medium transition-all ${
                      mode === m
                        ? 'bg-slate-800 text-white shadow-sm border border-slate-700'
                        : 'text-slate-500 hover:text-slate-300'
                    }`}
                  >
                    {m === 'login' ? '登录' : '注册'}
                  </button>
                ))}
              </div>

              {/* 表单字段 */}
              <div className="space-y-3" onKeyDown={onKeyDown}>
                <Field
                  icon={User}
                  placeholder="用户名"
                  value={username}
                  onChange={setUsername}
                  ref={inputRef}
                />
                {mode === 'register' && (
                  <Field icon={Tag} placeholder="显示名称（选填）" value={displayName} onChange={setDisplayName} />
                )}
                {mode === 'register' && (
                  <Field icon={Mail} placeholder="邮箱（选填）" value={email} onChange={setEmail} />
                )}
                <Field
                  icon={Lock}
                  type={showPwd ? 'text' : 'password'}
                  placeholder={mode === 'login' ? '密码' : '密码（至少 6 位）'}
                  value={password}
                  onChange={setPassword}
                  right={
                    <button
                      type="button"
                      onClick={() => setShowPwd(v => !v)}
                      className="text-slate-500 hover:text-slate-300 transition-colors"
                    >
                      {showPwd ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  }
                />
              </div>

              {/* 错误/成功提示 */}
              {error && (
                <div className="flex items-center gap-2 text-red-400 text-xs bg-red-950/40 border border-red-800/40 rounded-lg p-2.5">
                  <AlertCircle size={13} className="shrink-0" />
                  {error}
                </div>
              )}
              {success && (
                <div className="flex items-center gap-2 text-emerald-400 text-xs bg-emerald-950/40 border border-emerald-800/40 rounded-lg p-2.5">
                  <CheckCircle2 size={13} className="shrink-0" />
                  {success}
                </div>
              )}

              {/* 提交 */}
              <button
                onClick={handleSubmit}
                disabled={loading}
                className={`
                  w-full flex items-center justify-center gap-2 py-3 rounded-xl text-sm font-semibold transition-all
                  ${loading
                    ? 'bg-slate-700 text-slate-400 cursor-not-allowed'
                    : 'bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white'
                  }
                `}
              >
                {loading ? (
                  <><Loader2 size={15} className="animate-spin" /> 处理中…</>
                ) : mode === 'login' ? (
                  <><LogIn size={15} /> 登录</>
                ) : (
                  <><UserPlus size={15} /> 注册</>
                )}
              </button>

              <p className="text-center text-slate-600 text-xs">
                {mode === 'login' ? '还没有账号？' : '已有账号？'}
                <button
                  onClick={() => switchMode(mode === 'login' ? 'register' : 'login')}
                  className="text-cyan-500 hover:text-cyan-400 ml-1 transition-colors"
                >
                  {mode === 'login' ? '立即注册' : '去登录'}
                </button>
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
