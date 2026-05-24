// DatasetProductionScreen.jsx — Step 2: 数据集自动生产
import React, { useState, useEffect, useRef } from 'react';
import {
  Cpu, CheckCircle, Clock, AlertTriangle, ChevronRight,
  Database, Layers, Zap, SkipForward, BarChart2,
} from 'lucide-react';
import { apiClient } from './api';

// ── 流水线阶段展示配置 ─────────────────────────────────────────────
const STAGE_META = {
  init:          { label: '初始化',   pct: 5,  color: 'text-slate-400' },
  annotating:    { label: '自动标注', pct: 25, color: 'text-blue-400'  },
  scoring:       { label: '质量评分', pct: 50, color: 'text-amber-400' },
  deduplicating: { label: '去重清洗', pct: 68, color: 'text-purple-400'},
  packing:       { label: '打包导出', pct: 85, color: 'text-cyan-400'  },
  settling:      { label: '分润结算', pct: 95, color: 'text-green-400' },
  done:          { label: '生产完成', pct: 100,color: 'text-emerald-400'},
  failed:        { label: '生产失败', pct: 0,  color: 'text-red-400'   },
};

const STAGE_ORDER = ['init','annotating','scoring','deduplicating','packing','settling','done'];

export default function DatasetProductionScreen({ materialId, assetData, assetCategory, onProduced, onSkip }) {
  const [phase, setPhase] = useState('idle');   // idle | producing | done | error
  const [jobId, setJobId] = useState(null);
  const [jobState, setJobState] = useState(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [datasetType, setDatasetType] = useState('sft');
  const [domain, setDomain] = useState('');
  const [name, setName] = useState('');
  const esRef = useRef(null);

  // 自动推断域名
  useEffect(() => {
    if (!assetData) return;
    const txt = assetData.toLowerCase();
    if (txt.includes('医') || txt.includes('诊断') || txt.includes('病')) setDomain('medical');
    else if (txt.includes('法') || txt.includes('合同') || txt.includes('庭')) setDomain('legal');
    else if (txt.includes('代码') || txt.includes('算法') || txt.includes('编程')) setDomain('code_tech');
    else if (txt.includes('教') || txt.includes('课') || txt.includes('学')) setDomain('education');
    else setDomain('general');
  }, [assetData]);

  const startProduction = async () => {
    setPhase('producing');
    setErrorMsg('');
    setJobState(null);

    const jobName = name.trim() || `${domain}_${datasetType}_${Date.now()}`;
    try {
      // 1. 启动流水线
      const res = await apiClient.post('/api/dataset/produce', {
        material_ids:     [materialId],
        dataset_type:     datasetType,
        name:             jobName,
        domain:           domain,
        annotation_mode:  'auto',
      });
      const { job_id } = res;
      setJobId(job_id);

      // 2. SSE 订阅进度
      const es = new EventSource(`${apiClient.baseUrl}/api/dataset/job/${job_id}/stream`);
      esRef.current = es;

      es.onmessage = (evt) => {
        const data = JSON.parse(evt.data);
        setJobState(data);
        if (data.stage === 'done') {
          es.close();
          setPhase('done');
          // 拉取包详情
          apiClient.get(`/api/dataset/package/${data.package_id}`)
            .then(pkg => onProduced(pkg))
            .catch(() => setErrorMsg('包详情拉取失败，请手动进入下一步'));
        }
        if (data.stage === 'failed') {
          es.close();
          setPhase('error');
          setErrorMsg(data.error || '生产失败，请检查素材或稍后重试');
        }
      };
      es.onerror = () => {
        es.close();
        setPhase('error');
        setErrorMsg('进度推送连接中断，请刷新页面重试');
      };
    } catch (e) {
      setPhase('error');
      setErrorMsg(e?.message || '启动生产任务失败');
    }
  };

  const currentStage  = jobState?.stage || 'init';
  const stageMeta     = STAGE_META[currentStage] || STAGE_META.init;
  const doneStages    = STAGE_ORDER.slice(0, STAGE_ORDER.indexOf(currentStage) + 1);
  const progressPct   = stageMeta.pct;

  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center p-6">
      <div className="w-full max-w-xl space-y-6">

        {/* 标题 */}
        <div className="text-center space-y-1">
          <div className="flex items-center justify-center gap-2 text-cyan-400">
            <Cpu className="w-5 h-5" />
            <span className="text-sm font-mono uppercase tracking-widest">Dataset Production</span>
          </div>
          <h1 className="text-2xl font-bold text-slate-100">自动标注 & 数据集生产</h1>
          <p className="text-slate-400 text-sm">
            LLM 自动完成标注 → 七维质检 → 三级去重 → 多格式打包
          </p>
        </div>

        {/* 配置区（idle 阶段显示） */}
        {phase === 'idle' && (
          <div className="bg-slate-900 border border-slate-700 rounded-2xl p-5 space-y-4">
            {/* 数据集类型 */}
            <div>
              <label className="text-xs text-slate-400 font-mono mb-2 block">数据集类型</label>
              <div className="grid grid-cols-4 gap-2">
                {[
                  { v: 'sft',       l: 'SFT', desc: '指令微调' },
                  { v: 'dpo',       l: 'DPO', desc: '偏好对' },
                  { v: 'pretrain',  l: 'PT',  desc: '预训练' },
                  { v: 'multimodal',l: 'VLM', desc: '多模态' },
                ].map(t => (
                  <button
                    key={t.v}
                    onClick={() => setDatasetType(t.v)}
                    className={`py-2 rounded-lg border text-xs font-bold transition-all ${
                      datasetType === t.v
                        ? 'bg-cyan-900/40 border-cyan-500 text-cyan-300'
                        : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-500'
                    }`}
                  >
                    <div>{t.l}</div>
                    <div className="font-normal text-[10px] opacity-70">{t.desc}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* 领域 & 包名 */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-slate-400 font-mono mb-1 block">领域（自动推断）</label>
                <input
                  value={domain}
                  onChange={e => setDomain(e.target.value)}
                  placeholder="medical / legal / code_tech …"
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-cyan-500"
                />
              </div>
              <div>
                <label className="text-xs text-slate-400 font-mono mb-1 block">数据集包名</label>
                <input
                  value={name}
                  onChange={e => setName(e.target.value)}
                  placeholder={`${domain}_${datasetType}_pack`}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-cyan-500"
                />
              </div>
            </div>

            {/* 预览 */}
            <div className="bg-slate-800/60 rounded-lg p-3 text-xs text-slate-400 font-mono">
              <span className="text-slate-500">素材预览 · </span>
              {assetData?.slice(0, 80)}{assetData?.length > 80 ? '…' : ''}
            </div>

            {/* 操作按钮 */}
            <div className="flex gap-3">
              <button
                onClick={startProduction}
                className="flex-1 flex items-center justify-center gap-2 py-3 bg-cyan-600 hover:bg-cyan-500 text-white rounded-xl font-bold transition-all"
              >
                <Zap className="w-4 h-4" />
                开始自动生产
              </button>
              <button
                onClick={onSkip}
                className="flex items-center gap-1.5 px-4 py-3 border border-slate-700 text-slate-400 hover:text-slate-200 hover:border-slate-500 rounded-xl text-sm transition-all"
              >
                <SkipForward className="w-4 h-4" />
                跳过
              </button>
            </div>
          </div>
        )}

        {/* 生产进度区 */}
        {(phase === 'producing' || phase === 'done') && (
          <div className="bg-slate-900 border border-slate-700 rounded-2xl p-5 space-y-4">
            {/* 总进度条 */}
            <div>
              <div className="flex justify-between text-xs font-mono mb-1">
                <span className={stageMeta.color}>{stageMeta.label}</span>
                <span className="text-slate-500">{progressPct}%</span>
              </div>
              <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-cyan-500 transition-all duration-700"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
            </div>

            {/* 阶段步骤列表 */}
            <div className="space-y-2">
              {STAGE_ORDER.filter(s => s !== 'failed').map(s => {
                const done    = doneStages.includes(s) && s !== currentStage;
                const current = s === currentStage && phase !== 'done';
                const meta    = STAGE_META[s];
                return (
                  <div key={s} className={`flex items-center gap-2 text-xs font-mono transition-all ${
                    done ? 'text-emerald-400' : current ? meta.color : 'text-slate-700'
                  }`}>
                    {done    ? <CheckCircle className="w-3.5 h-3.5 shrink-0" /> :
                     current ? <Clock className="w-3.5 h-3.5 shrink-0 animate-pulse" /> :
                               <div className="w-3.5 h-3.5 rounded-full border border-slate-700 shrink-0" />}
                    {meta.label}
                  </div>
                );
              })}
            </div>

            {/* 实时数字 */}
            {jobState && (
              <div className="grid grid-cols-3 gap-2 pt-1 border-t border-slate-800">
                {[
                  { label: '已标注', val: jobState.annotated },
                  { label: '已质检', val: jobState.scored },
                  { label: '已打包', val: jobState.packed },
                ].map(item => (
                  <div key={item.label} className="text-center">
                    <div className="text-lg font-bold text-slate-100">{item.val ?? 0}</div>
                    <div className="text-[10px] text-slate-500 font-mono">{item.label}</div>
                  </div>
                ))}
              </div>
            )}

            {phase === 'done' && (
              <div className="flex items-center gap-2 text-emerald-400 text-sm font-bold">
                <CheckCircle className="w-5 h-5" />
                生产完成，正在进入估值环节…
              </div>
            )}
          </div>
        )}

        {/* 错误区 */}
        {phase === 'error' && (
          <div className="bg-red-950/40 border border-red-800/50 rounded-2xl p-5 space-y-3">
            <div className="flex items-center gap-2 text-red-400 font-bold">
              <AlertTriangle className="w-5 h-5" />
              生产失败
            </div>
            <p className="text-red-300/80 text-sm">{errorMsg}</p>
            <div className="flex gap-3">
              <button
                onClick={() => setPhase('idle')}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-sm transition-all"
              >
                重新配置
              </button>
              <button
                onClick={onSkip}
                className="px-4 py-2 border border-slate-700 text-slate-400 hover:text-slate-200 rounded-lg text-sm transition-all"
              >
                跳过此步骤
              </button>
            </div>
          </div>
        )}

        {/* 说明 */}
        <div className="grid grid-cols-3 gap-3 text-center text-xs text-slate-500">
          <div className="flex flex-col items-center gap-1">
            <Layers className="w-4 h-4 text-slate-600" />
            SFT / DPO / PT 三格式
          </div>
          <div className="flex flex-col items-center gap-1">
            <BarChart2 className="w-4 h-4 text-slate-600" />
            七维质检自动分档
          </div>
          <div className="flex flex-col items-center gap-1">
            <Database className="w-4 h-4 text-slate-600" />
            JSONL + Parquet 打包
          </div>
        </div>
      </div>
    </div>
  );
}
