// src/BatchUploadPanel.jsx — 批量素材上传面板
/**
 * 知数知圈 · 批量上传组件
 * 支持拖放 / 点击上传 CSV、JSONL、ZIP、TXT
 * 上传完成后回调 onUploaded(materialIds[])，供后续生产任务使用
 */
import React, { useState, useRef, useCallback } from 'react';
import {
  UploadCloud, FileText, Archive, CheckCircle2,
  AlertCircle, X, ChevronRight, Loader2, Info,
} from 'lucide-react';
import { tokenStore } from './api';

// ── 常量 ─────────────────────────────────────────────────────────
const ACCEPT = '.csv,.jsonl,.ndjson,.zip,.txt,.md';
const MAX_MB = 20;

const FORMAT_HINTS = [
  { ext: 'CSV',  desc: '必须有 content 列，可选 material_type / domain / tags' },
  { ext: 'JSONL', desc: '每行 {"content":"...", "domain":"...", "tags":"..."}' },
  { ext: 'ZIP',  desc: '内含 .txt / .md 文件，每个文件一条素材' },
  { ext: 'TXT',  desc: '按空行（\\n\\n）分段，每段作为一条素材' },
];

// ── 工具函数 ──────────────────────────────────────────────────────
function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

// ════════════════════════════════════════════════════════════════════
export default function BatchUploadPanel({ onUploaded, onCancel }) {
  const [dragOver, setDragOver]     = useState(false);
  const [file, setFile]             = useState(null);
  const [phase, setPhase]           = useState('idle'); // idle | uploading | done | error
  const [result, setResult]         = useState(null);
  const [errorMsg, setErrorMsg]     = useState('');
  const [showFormats, setShowFormats] = useState(false);
  const inputRef = useRef(null);

  // ── 文件选择 ─────────────────────────────────────────────────────
  const selectFile = useCallback((f) => {
    if (!f) return;
    if (f.size > MAX_MB * 1024 * 1024) {
      setErrorMsg(`文件超过 ${MAX_MB}MB 限制（当前 ${fmtSize(f.size)}）`);
      setPhase('error');
      return;
    }
    setFile(f);
    setPhase('idle');
    setErrorMsg('');
    setResult(null);
  }, []);

  const onInputChange = (e) => selectFile(e.target.files?.[0]);
  const onDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    selectFile(e.dataTransfer.files?.[0]);
  };

  // ── 上传 ─────────────────────────────────────────────────────────
  const upload = async () => {
    if (!file) return;
    setPhase('uploading');
    setErrorMsg('');

    const token = tokenStore.get();
    if (!token) {
      setErrorMsg('请先登录后再上传素材');
      setPhase('error');
      return;
    }

    const form = new FormData();
    form.append('file', file);

    try {
      const res = await fetch('/api/dataset/batch_ingest', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      });

      if (res.status === 401) {
        setErrorMsg('登录已过期，请重新登录');
        setPhase('error');
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setErrorMsg(body.detail?.detail || body.detail || `上传失败（${res.status}）`);
        setPhase('error');
        return;
      }

      const data = await res.json();
      setResult(data);
      setPhase('done');
    } catch (err) {
      setErrorMsg(err.message || '网络错误，请检查后端是否运行');
      setPhase('error');
    }
  };

  const reset = () => {
    setFile(null); setPhase('idle'); setResult(null); setErrorMsg('');
    if (inputRef.current) inputRef.current.value = '';
  };

  // ── 渲染 ─────────────────────────────────────────────────────────
  return (
    <div className="w-full max-w-xl mx-auto space-y-4">

      {/* 标题行 */}
      <div className="flex items-center justify-between">
        <h3 className="text-white font-semibold text-base tracking-wide flex items-center gap-2">
          <UploadCloud size={18} className="text-cyan-400" />
          批量上传素材
        </h3>
        <button
          onClick={() => setShowFormats(v => !v)}
          className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200 transition-colors"
        >
          <Info size={13} />
          {showFormats ? '收起格式说明' : '支持格式'}
        </button>
      </div>

      {/* 格式说明 */}
      {showFormats && (
        <div className="rounded-lg border border-slate-700/60 bg-slate-900/60 p-3 space-y-1.5">
          {FORMAT_HINTS.map(h => (
            <div key={h.ext} className="flex gap-2 text-xs">
              <span className="shrink-0 font-mono text-cyan-400 w-10">{h.ext}</span>
              <span className="text-slate-400">{h.desc}</span>
            </div>
          ))}
        </div>
      )}

      {/* 拖放区 */}
      {phase !== 'done' && (
        <div
          onDragOver={e => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onClick={() => !file && inputRef.current?.click()}
          className={`
            relative rounded-xl border-2 border-dashed transition-all duration-200 cursor-pointer
            flex flex-col items-center justify-center gap-3 p-8 text-center
            ${dragOver
              ? 'border-cyan-400 bg-cyan-950/30'
              : file
                ? 'border-slate-600 bg-slate-900/40 cursor-default'
                : 'border-slate-700 bg-slate-900/20 hover:border-slate-500 hover:bg-slate-900/40'
            }
          `}
        >
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            className="hidden"
            onChange={onInputChange}
          />

          {!file ? (
            <>
              <div className="w-12 h-12 rounded-full bg-slate-800 flex items-center justify-center">
                <UploadCloud size={24} className="text-slate-400" />
              </div>
              <div>
                <p className="text-slate-300 text-sm font-medium">拖放文件到这里，或点击选择</p>
                <p className="text-slate-500 text-xs mt-1">支持 CSV / JSONL / ZIP / TXT，最大 {MAX_MB}MB</p>
              </div>
            </>
          ) : (
            <div className="w-full flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-slate-800 flex items-center justify-center shrink-0">
                {file.name.endsWith('.zip') ? (
                  <Archive size={20} className="text-amber-400" />
                ) : (
                  <FileText size={20} className="text-cyan-400" />
                )}
              </div>
              <div className="text-left flex-1 min-w-0">
                <p className="text-slate-200 text-sm font-medium truncate">{file.name}</p>
                <p className="text-slate-500 text-xs">{fmtSize(file.size)}</p>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); reset(); }}
                className="p-1.5 rounded-lg hover:bg-slate-700 text-slate-400 hover:text-white transition-colors"
              >
                <X size={16} />
              </button>
            </div>
          )}
        </div>
      )}

      {/* 错误提示 */}
      {phase === 'error' && errorMsg && (
        <div className="flex gap-2 items-start rounded-lg bg-red-950/40 border border-red-800/50 p-3 text-sm text-red-300">
          <AlertCircle size={16} className="shrink-0 mt-0.5 text-red-400" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* 成功结果 */}
      {phase === 'done' && result && (
        <div className="rounded-xl border border-emerald-800/50 bg-emerald-950/30 p-4 space-y-3">
          <div className="flex items-center gap-2 text-emerald-400 font-semibold text-sm">
            <CheckCircle2 size={18} />
            上传成功
          </div>

          {/* 统计 */}
          <div className="grid grid-cols-3 gap-2 text-center">
            <StatBox label="成功入库" value={result.uploaded} color="text-emerald-300" />
            <StatBox label="跳过" value={result.skipped} color="text-amber-300" />
            <StatBox label="失败" value={result.failed ?? 0} color="text-red-300" />
          </div>

          {/* 解析警告 */}
          {result.errors?.length > 0 && (
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer hover:text-slate-300 select-none">
                {result.errors.length} 条解析警告
              </summary>
              <ul className="mt-1.5 space-y-0.5 max-h-28 overflow-y-auto pl-2">
                {result.errors.map((e, i) => (
                  <li key={i} className="text-amber-500/80">• {e}</li>
                ))}
              </ul>
            </details>
          )}

          {/* 操作 */}
          <div className="flex gap-2 pt-1">
            <button
              onClick={reset}
              className="flex-1 py-2 rounded-lg border border-slate-700 text-slate-300
                         hover:bg-slate-800 text-sm transition-colors"
            >
              继续上传
            </button>
            <button
              onClick={() => onUploaded(result.material_ids)}
              className="flex-1 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white
                         text-sm font-medium transition-colors flex items-center justify-center gap-1"
            >
              发起生产任务 <ChevronRight size={15} />
            </button>
          </div>
        </div>
      )}

      {/* 操作按钮 */}
      {phase !== 'done' && (
        <div className="flex gap-2">
          {onCancel && (
            <button
              onClick={onCancel}
              className="flex-1 py-2.5 rounded-lg border border-slate-700 text-slate-400
                         hover:text-slate-200 hover:bg-slate-800/60 text-sm transition-colors"
            >
              取消
            </button>
          )}
          <button
            onClick={upload}
            disabled={!file || phase === 'uploading'}
            className={`
              flex-1 py-2.5 rounded-lg text-sm font-semibold transition-all
              flex items-center justify-center gap-2
              ${!file || phase === 'uploading'
                ? 'bg-slate-700 text-slate-500 cursor-not-allowed'
                : 'bg-cyan-600 hover:bg-cyan-500 text-white'}
            `}
          >
            {phase === 'uploading' ? (
              <><Loader2 size={15} className="animate-spin" /> 上传中…</>
            ) : (
              <><UploadCloud size={15} /> 开始上传</>
            )}
          </button>
        </div>
      )}
    </div>
  );
}

function StatBox({ label, value, color }) {
  return (
    <div className="rounded-lg bg-slate-900/60 py-2 px-1">
      <div className={`text-xl font-bold font-mono ${color}`}>{value}</div>
      <div className="text-slate-500 text-xs mt-0.5">{label}</div>
    </div>
  );
}
