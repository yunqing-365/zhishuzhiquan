import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  UploadCloud, Cpu, Database, PlayCircle, ShieldCheck, Lock,
  Image as ImageIcon, FileText, Tag, ChevronDown, ChevronUp,
  FlaskConical, Mic, StopCircle, Waveform, Film, History,
} from 'lucide-react';
import { apiClient } from './api';

// ─── Demo 预设（★ v4: 补充音频场景）────────────────────────────────────
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
  // ★ v5 视频预设
  {
    label: '电影级短片 (film_cinematic)',
    category: 'video',
    description: '4K电影级短片，专业摄影机拍摄，LOG格式调色，多镜头切换，手持+稳定器混用，内容：城市夜景空镜与人物情感独白，时长3分28秒，配乐原创，首发未授权。',
  },
  {
    label: '医疗手术纪录片 (documentary_medical)',
    category: 'video',
    description: '医院手术室纪录片片段，腹腔镜微创手术全程记录，专业医疗摄像机，清晰度4K，涵盖术前准备、手术操作、术后处置完整流程，用于医学教学授权素材库。',
  },
  {
    label: '教学讲解视频 (edu_lecture)',
    category: 'video',
    description: '高校公开课视频，量子计算基础理论讲解，教授口述配合白板推导，配备字幕与章节标记，30分钟完整课时，画质1080P，音质清晰，适合AI教育训练集。',
  },
  // ★ v4 音频预设
  {
    label: '临床语音转录 (speech_medical)',
    category: 'audio',
    description: '医院手术室术后访谈录音，临床医生口述：患者诊断为慢性肾功能衰竭，肌酐水平458μmol/L，建议透析治疗并转上级医院。',
  },
  {
    label: '庭审证词录音 (speech_legal)',
    category: 'audio',
    description: '法庭庭审现场录音，被告律师陈述：根据《合同法》第52条，本合同因存在重大误解应当认定无效，请求法院予以撤销。',
  },
  {
    label: '原创纯音乐 (music_original)',
    category: 'audio',
    description: '作曲家原创钢琴独奏曲，浪漫主义风格，主题旋律重复变奏，配以弦乐编配，完整曲目时长4分32秒，未发表首版录音。',
  },
];

// ─── 场景覆盖选项（★ v4: 补充音频细粒度场景）───────────────────────────
// ─── 场景图标映射 ──────────────────────────────────────────────────
const SCENE_ICONS = {
  medical_sft:     '🏥', legal_doc:       '⚖️',  code_tech:       '💻',
  creative:        '✍️',  chat_qa:         '💬', general:         '🔧',
  illustration:    '🎨', photo:           '📷', diagram:         '📊',
  screenshot:      '🖥️',  noise:           '🚫',
  speech_medical:  '🏥', speech_legal:    '⚖️',  speech_edu:      '📚',
  music_original:  '🎵', ambient_sfx:     '🌿',
  documentary:     '📹', lecture:         '🎓', cinematic:       '🎬',
  sports_action:   '⚽', vlog:            '📱',
  // 旧版前端键名兼容
  vid_cinematic:   '🎬', vid_doc:         '📹', vid_edu:         '🎓',
  vid_user_gen:    '📱',
};

// ─── 静态兜底数据（后端未就绪时使用）─────────────────────────────
const _FALLBACK_SCENE_OPTIONS = [
  { value: '',              label: '🤖 自动识别 (推荐)',    group: 'auto'  },
  { value: 'medical_sft',  label: '🏥 医疗 SFT  ×1.35',   group: 'text'  },
  { value: 'legal_doc',    label: '⚖️  法律文书  ×1.20',   group: 'text'  },
  { value: 'code_tech',    label: '💻 代码技术  ×1.10',   group: 'text'  },
  { value: 'creative',     label: '✍️  创意写作  ×0.90',   group: 'text'  },
  { value: 'chat_qa',      label: '💬 问答对话  ×0.80',   group: 'text'  },
  { value: 'illustration', label: '🎨 原创插画  ×1.50',   group: 'image' },
  { value: 'photo',        label: '📷 摄影作品  ×1.00',   group: 'image' },
  { value: 'diagram',      label: '📊 图表图解  ×0.55',   group: 'image' },
  { value: 'screenshot',   label: '🖥️  截图素材  ×0.25',   group: 'image' },
  { value: 'speech_medical', label: '🏥 医疗语音  ×1.40', group: 'audio' },
  { value: 'speech_legal',   label: '⚖️  法律语音  ×1.25', group: 'audio' },
  { value: 'speech_edu',     label: '📚 教育语音  ×0.85', group: 'audio' },
  { value: 'music_original', label: '🎵 原创音乐  ×1.10', group: 'audio' },
  { value: 'ambient_sfx',    label: '🌿 环境音效  ×0.60', group: 'audio' },
  { value: 'noise',          label: '🚫 噪声/废话  ×0.05', group: 'audio' },
  { value: 'documentary',  label: '📹 纪录/访谈  ×1.40',  group: 'video' },
  { value: 'lecture',       label: '🎓 教学讲解  ×1.30',  group: 'video' },
  { value: 'cinematic',     label: '🎬 影视创作  ×1.20',  group: 'video' },
  { value: 'sports_action', label: '⚽ 运动/动作  ×0.90',  group: 'video' },
  { value: 'vlog',          label: '📱 个人 vlog  ×0.65',  group: 'video' },
];

/**
 * 将 /api/scenes 响应转换为 SCENE_OVERRIDE_OPTIONS 格式
 * 支持: text_scenes / image_scenes / audio_scenes / video_scene_weights
 */
function buildSceneOptions(scenesData) {
  if (!scenesData) return _FALLBACK_SCENE_OPTIONS;
  const options = [{ value: '', label: '🤖 自动识别 (推荐)', group: 'auto' }];

  const addGroup = (weights, group) => {
    if (!weights || typeof weights !== 'object') return;
    Object.entries(weights).forEach(([key, weight]) => {
      const icon  = SCENE_ICONS[key] || '🔧';
      const label = `${icon} ${key}  ×${weight}`;
      options.push({ value: key, label, group, weight });
    });
  };

  addGroup(scenesData.text_scenes,        'text');
  addGroup(scenesData.image_scenes,       'image');
  addGroup(scenesData.audio_scenes,       'audio');
  addGroup(scenesData.video_scene_weights,'video');

  return options.length > 1 ? options : _FALLBACK_SCENE_OPTIONS;
}

// ─── 波形可视化组件（仅在有 AudioContext 时渲染）──────────────────────
const WaveformCanvas = ({ analyserRef, isRecording }) => {
  const canvasRef = useRef(null);
  const rafRef    = useRef(null);

  useEffect(() => {
    if (!isRecording || !analyserRef.current) return;
    const analyser = analyserRef.current;
    const buf = new Uint8Array(analyser.fftSize);
    const draw = () => {
      rafRef.current = requestAnimationFrame(draw);
      analyser.getByteTimeDomainData(buf);
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.strokeStyle = '#34d399';
      ctx.lineWidth   = 1.5;
      ctx.beginPath();
      const sliceW = canvas.width / buf.length;
      let x = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = buf[i] / 128;
        const y = (v * canvas.height) / 2;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        x += sliceW;
      }
      ctx.stroke();
    };
    draw();
    return () => cancelAnimationFrame(rafRef.current);
  }, [isRecording, analyserRef]);

  return (
    <canvas
      ref={canvasRef}
      width={360} height={48}
      className="w-full h-12 rounded-lg bg-slate-950/80"
    />
  );
};

// ─── 主组件 ─────────────────────────────────────────────────────────
const DataInputScreen = ({ onComplete, onMaterialUploaded, onHistory }) => {
  const [isProcessing, setIsProcessing] = useState(false);
  const [progress, setProgress]         = useState(0);
  const [statusText, setStatusText]     = useState('');
  const [enableZK, setEnableZK]         = useState(true);
  const [assetCategory, setAssetCategory] = useState('image');
  const [inputText, setInputText]       = useState('');
  const [selectedImage, setSelectedImage] = useState(null);
  const [imageB64, setImageB64]           = useState(null);         // base64 图像
  const [activePreset, setActivePreset] = useState(null);
  const [sceneOverride, setSceneOverride] = useState('');
  const [showDebug, setShowDebug]       = useState(false);

  // ★ v6: 动态场景配置（从 /api/scenes 加载）
  const [sceneOptions, setSceneOptions]     = useState(_FALLBACK_SCENE_OPTIONS);
  const [scenesLoaded, setScenesLoaded]     = useState(false);
  const [dualStreamInfo, setDualStreamInfo] = useState(null);  // Stage C 双流信息

  // ★ v4 音频状态
  const [isRecording, setIsRecording]   = useState(false);
  const [audioB64, setAudioB64]         = useState(null);       // base64 wav
  const [audioDuration, setAudioDuration] = useState(0);        // 秒
  const [audioFileName, setAudioFileName] = useState(null);     // 上传文件名
  const [recSeconds, setRecSeconds]     = useState(0);          // 录音计时

  // ★ v5 视频状态
  const [videoB64, setVideoB64]           = useState(null);       // base64 video
  const [videoFileName, setVideoFileName] = useState(null);       // 文件名
  const [videoMeta, setVideoMeta]         = useState(null);       // { size, type }

  const mediaRecorderRef   = useRef(null);
  const chunksRef          = useRef([]);
  const analyserRef        = useRef(null);
  const audioCtxRef        = useRef(null);
  const timerRef           = useRef(null);
  const fileInputRef       = useRef(null);
  const videoFileInputRef  = useRef(null);

  // ★ v6: 从 /api/scenes 动态加载场景权重 ──────────────────────────
  useEffect(() => {
    let cancelled = false;
    apiClient.scenes()
      .then(data => {
        if (cancelled) return;
        const opts = buildSceneOptions(data);
        setSceneOptions(opts);
        setScenesLoaded(true);
        // 提取 Stage C 双流推理信息
        if (data?.video_dual_stream) {
          setDualStreamInfo(data.video_dual_stream);
        }
      })
      .catch(() => {
        // 后端未启动时静默使用 fallback，不影响主流程
        if (!cancelled) setScenesLoaded(false);
      });
    return () => { cancelled = true; };
  }, []);

  // ─ 清理录音资源
  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
    clearInterval(timerRef.current);
    if (audioCtxRef.current) { audioCtxRef.current.close(); audioCtxRef.current = null; }
    analyserRef.current = null;
    setIsRecording(false);
  }, []);

  // 页面卸载时自动停录
  useEffect(() => () => stopRecording(), [stopRecording]);

  // ─ 开始录音
  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // Web Audio 分析器（供波形可视化用）
      const ctx      = new AudioContext();
      const source   = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      audioCtxRef.current  = ctx;
      analyserRef.current  = analyser;

      // MediaRecorder
      const mr = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      chunksRef.current = [];
      mr.ondataavailable = (e) => e.data.size > 0 && chunksRef.current.push(e.data);
      mr.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        const reader = new FileReader();
        reader.onloadend = () => {
          const b64 = reader.result.split(',')[1];
          setAudioB64(b64);
          setAudioDuration(Math.round(blob.size / 8000)); // 粗略估算
        };
        reader.readAsDataURL(blob);
        // 停止 stream 轨道
        stream.getTracks().forEach((t) => t.stop());
      };
      mr.start();
      mediaRecorderRef.current = mr;

      // 计时
      let secs = 0;
      setRecSeconds(0);
      timerRef.current = setInterval(() => { secs++; setRecSeconds(secs); }, 1000);
      setIsRecording(true);
      setAudioB64(null);
      setAudioFileName(null);
    } catch (err) {
      alert('无法访问麦克风，请检查浏览器权限。');
    }
  };

  // ─ 上传音频文件
  const handleAudioFile = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onloadend = () => {
      setAudioB64(reader.result.split(',')[1]);
      setAudioFileName(file.name);
      setAudioDuration(0);
    };
    reader.readAsDataURL(file);
    setAudioB64(null);
  };

  const getDescriptionForBackend = () => {
    if (assetCategory === 'text')  return inputText;
    if (assetCategory === 'audio') return inputText || '音频语料';
    if (assetCategory === 'video') return inputText || '视频影像';
    return inputText || '原创图像';
  };

  const handlePreset = (preset) => {
    setActivePreset(preset.label);
    setAssetCategory(preset.category);
    setInputText(preset.description);
    setSelectedImage(preset.category === 'image' ? '[Demo 预设图像]' : null);
    setImageB64(null);
    setAudioB64(null);
    setAudioFileName(null);
    setVideoB64(null);
    setVideoFileName(null);
    setVideoMeta(null);
    setSceneOverride('');
    if (isRecording) stopRecording();
  };

  // ─ 切换模态时清空状态
  const switchCategory = (cat) => {
    if (isRecording) stopRecording();
    setAssetCategory(cat);
    setInputText('');
    setSelectedImage(null);
    setAudioB64(null);
    setAudioFileName(null);
    setImageB64(null);
    setVideoB64(null);
    setVideoFileName(null);
    setVideoMeta(null);
    setActivePreset(null);
    setSceneOverride('');
  };

  // ─ 上传视频文件
  const handleVideoFile = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onloadend = () => {
      setVideoB64(reader.result.split(',')[1]);
      setVideoFileName(file.name);
      setVideoMeta({ size: file.size, type: file.type });
    };
    reader.readAsDataURL(file);
    setVideoB64(null);
  };

  const getProcessingSteps = (category, isZk) => {
    const steps = [];
    const overrideNote = sceneOverride ? ` [强制场景: ${sceneOverride}]` : '';
    if (isZk) {
      steps.push({ p: 10, text: '【Stage 1 — 模态路由】初始化 WebAssembly 沙箱，识别模态类型...' });
      if (category === 'audio') {
        steps.push({ p: 28, text: `【Stage 2 — 声场分类】SceneClassifier v4 双通道融合：声学(ZCR/HNR/chroma_var×0.65) + 文本关键词(×0.35)${overrideNote}` });
        steps.push({ p: 45, text: '【Stage 3 — 音频特征】AudioAdapter: 频谱质心 + 谐波噪声比 + 节拍强度 + SNR 评估...' });
        steps.push({ p: 62, text: '【Stage 3 — 细粒度标签】speech_medical / speech_legal / music_original / ambient_sfx 子场景匹配...' });
      } else if (category === 'image') {
        steps.push({ p: 28, text: `【Stage 2 — 场景分类】SceneClassifier v4 分析图像描述，识别子场景...${overrideNote}` });
        steps.push({ p: 45, text: '【Stage 3 — 特征提取】ImageAdapter: LAION-Aesthetics 美学评估 + DWT 隐写鲁棒性...' });
        steps.push({ p: 62, text: '【Stage 3 — 稀缺度】CLIP 512维特征对齐，计算画派风格稀缺度...' });
      } else if (category === 'video') {
        steps.push({ p: 28, text: `【Stage 2 — 场景分类】VideoAdapter v1 Stage-B: 图像分类器代理 + 描述关键词${overrideNote}` });
        steps.push({ p: 45, text: '【Stage 3 — 帧采样】OpenCV 均匀采样16帧 → CLIP ViT-B/32 per-frame 特征提取...' });
        steps.push({ p: 62, text: '【Stage 3 — 时序聚合】帧特征 mean+std → 时域多样性 + 镜头切换密度 + CLIP 美学评分...' });
      } else {
        steps.push({ p: 28, text: `【Stage 2 — 场景分类】SceneClassifier v4 分析文本领域，识别子场景...${overrideNote}` });
        steps.push({ p: 45, text: '【Stage 3 — 特征提取】TextAdapter: 场景专项 SNR + Shannon 熵 + 废话熔断检测...' });
        steps.push({ p: 62, text: '【Stage 3 — 图谱构建】GraphRAG 实体拓扑密度 + KNN-Shapley 边际贡献评估...' });
      }
      steps.push({ p: 78,  text: '【Stage 4 — TEV 标准化】双层乘数 (模态权重 × 场景子权重) 映射至统一定价框架...' });
      steps.push({ p: 92,  text: '【zk-SNARK】生成零知识证明凭证，本地明文安全销毁...' });
      steps.push({ p: 100, text: '凭证上链成功！向预言机节点发起 RPC 定价请求...' });
    } else {
      steps.push({ p: 50,  text: `正在将 ${category} 资产上传至中心化预言机...` });
      steps.push({ p: 100, text: '确权完成！准备进入统一定价框架...' });
    }
    return steps;
  };

  const processData = () => {
    const desc = getDescriptionForBackend();
    if (assetCategory === 'text'  && !desc.trim())   return alert('请输入文本内容');
    if (assetCategory === 'image' && !selectedImage)  return alert('请上传画作或选择 Demo 预设');
    if (assetCategory === 'audio' && !desc.trim() && !audioB64)
      return alert('请输入音频描述，或先录音/上传音频文件');
    if (assetCategory === 'video' && !desc.trim())
      return alert('请输入视频描述内容（Stage B 真实帧采样需上传视频文件）');

    if (isRecording) stopRecording();

    setIsProcessing(true);
    setProgress(0);

    const steps = getProcessingSteps(assetCategory, enableZK);
    let cur = 0;
    const interval = setInterval(async () => {
      if (cur < steps.length) {
        setProgress(steps[cur].p);
        setStatusText(steps[cur].text);
        cur++;
      } else {
        clearInterval(interval);

        // ── 如果有 onMaterialUploaded，先调 ingest API 拿 material_id ──
        if (onMaterialUploaded) {
          try {
            const { tokenStore } = await import('./api');
            const token = tokenStore.get();
            if (token) {
              const res = await fetch('/api/dataset/ingest', {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/json',
                  'Authorization': `Bearer ${token}`,
                },
                body: JSON.stringify({
                  material_type: assetCategory,
                  raw_content: desc,
                  metadata: {
                    scene_override: sceneOverride || null,
                    zk_mode: enableZK,
                  },
                }),
              });
              if (res.ok) {
                const { material_id } = await res.json();
                setTimeout(() => onMaterialUploaded(
                  material_id, desc, assetCategory,
                  audioB64 || null, imageB64 || null, videoB64 || null
                ), 400);
                return;
              }
            }
          } catch (e) {
            console.warn('[DataInputScreen] ingest 失败，降级到直接估值:', e.message);
          }
        }

        // 降级：无 token / ingest 失败 / 无 onMaterialUploaded → 旧路径直接估值
        setTimeout(() => onComplete(desc, assetCategory, enableZK, sceneOverride || null, audioB64 || null, imageB64 || null, videoB64 || null), 1200);
      }
    }, 750);
  };

  const overrideLabel    = sceneOptions.find(o => o.value === sceneOverride)?.label || '🤖 自动识别';
  const isOverrideActive = Boolean(sceneOverride);

  // 音频 preset 仅在音频标签下高亮
  const presetColor = (preset) => {
    if (preset.category === 'image') return 'text-amber-500';
    if (preset.category === 'audio') return 'text-emerald-500';
    if (preset.category === 'video') return 'text-violet-500';
    return 'text-blue-500';
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
          {/* ★ v4 版本标签 */}
          <div className="flex items-center gap-2 text-xs text-slate-500 font-mono">
            {onHistory && (
              <button
                onClick={onHistory}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-slate-700 text-slate-400 hover:text-violet-400 hover:border-violet-500/50 hover:bg-violet-900/10 transition-all mr-2"
                title="查看估值历史"
              >
                <History className="w-3.5 h-3.5" />
                <span className="text-xs font-mono">历史</span>
              </button>
            )}
            <Tag className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-emerald-400 font-semibold">Scene Classifier</span>
            <span className="px-1.5 py-0.5 rounded bg-emerald-900/40 border border-emerald-500/30 text-emerald-300 text-[10px] font-bold tracking-wider">v4 · dual-channel fusion</span>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">

          {/* LEFT */}
          <div className="space-y-4">

            {/* ★ v4 模态切换：三标签 */}
            <div className="flex space-x-1.5 bg-slate-950/50 p-1 rounded-xl border border-slate-800">
              <button
                onClick={() => switchCategory('image')}
                className={`flex-1 flex items-center justify-center py-2 text-xs font-bold rounded-lg transition-all ${assetCategory === 'image' ? 'bg-slate-800 text-amber-400 shadow-sm' : 'text-slate-500 hover:text-slate-300'}`}
              >
                <ImageIcon className="w-3.5 h-3.5 mr-1.5" /> 原创画作
              </button>
              <button
                onClick={() => switchCategory('text')}
                className={`flex-1 flex items-center justify-center py-2 text-xs font-bold rounded-lg transition-all ${assetCategory === 'text' ? 'bg-slate-800 text-blue-400 shadow-sm' : 'text-slate-500 hover:text-slate-300'}`}
              >
                <FileText className="w-3.5 h-3.5 mr-1.5" /> 文本语料
              </button>
              <button
                onClick={() => switchCategory('audio')}
                className={`flex-1 flex items-center justify-center py-2 text-xs font-bold rounded-lg transition-all ${assetCategory === 'audio' ? 'bg-slate-800 text-emerald-400 shadow-sm' : 'text-slate-500 hover:text-slate-300'}`}
              >
                <Mic className="w-3.5 h-3.5 mr-1.5" /> 音频语料
              </button>
              <button
                onClick={() => switchCategory('video')}
                className={`flex-1 flex items-center justify-center py-2 text-xs font-bold rounded-lg transition-all ${assetCategory === 'video' ? 'bg-slate-800 text-violet-400 shadow-sm' : 'text-slate-500 hover:text-slate-300'}`}
              >
                <Film className="w-3.5 h-3.5 mr-1.5" /> 视频影像
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

            {/* 文本输入区 */}
            {assetCategory === 'text' && (
              <textarea
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                placeholder={"输入需要确权的语料 (医疗 / 法律 / 代码 / 创意写作 / 问答对话)...\nScene Classifier v4 将自动识别领域场景并调整定价权重。"}
                className="w-full h-36 bg-slate-950/50 border border-slate-700 rounded-xl p-4 font-mono text-sm text-blue-400 placeholder-slate-600 focus:outline-none focus:border-blue-500 resize-none"
              />
            )}

            {/* 图像输入区 */}
            {assetCategory === 'image' && (
              <div className="space-y-3">
                <div
                  onClick={() => document.getElementById('img-upload-input').click()}
                  className={`w-full h-24 bg-slate-950/50 border-2 border-dashed rounded-xl flex flex-col items-center justify-center cursor-pointer transition-colors ${selectedImage ? 'border-amber-500 bg-amber-900/10' : 'border-slate-700 hover:border-slate-500'}`}
                >
                  {selectedImage
                    ? <><ShieldCheck className="w-6 h-6 text-amber-400 mb-1" /><p className="text-xs text-amber-400 font-bold">✓ 画作已加载入沙箱</p><p className="text-[10px] text-amber-600 mt-0.5 truncate max-w-[180px]">{selectedImage}</p></>
                    : <><UploadCloud className="w-6 h-6 text-slate-500 mb-1" /><p className="text-xs text-slate-300 font-bold">点击上传商业插画原稿</p><p className="text-[10px] text-slate-600 mt-0.5">支持 JPG / PNG / WEBP</p></>}
                </div>
                <input
                  id="img-upload-input"
                  type="file"
                  accept="image/*"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    setSelectedImage(file.name);
                    const reader = new FileReader();
                    reader.onloadend = () => setImageB64(reader.result.split(',')[1]);
                    reader.readAsDataURL(file);
                  }}
                />
                <div>
                  <p className="text-[10px] text-slate-500 mb-1.5 flex items-center gap-1">
                    <Tag className="w-3 h-3" />
                    描述画作场景/风格 <span className="text-purple-400">(Scene Classifier 依赖此字段)</span>
                  </p>
                  <textarea
                    value={inputText}
                    onChange={(e) => setInputText(e.target.value)}
                    placeholder={"例: 赛博朋克风格原创插画，机甲少女，精细光影，4k手绘，原画师作品\n或: 普通照片 / 浏览器截图 / 技术架构图..."}
                    className="w-full h-20 bg-slate-950/50 border border-slate-700 rounded-xl p-3 font-mono text-xs text-amber-400 placeholder-slate-600 focus:outline-none focus:border-amber-500 resize-none"
                  />
                </div>
              </div>
            )}

            {/* ★ v5 视频输入区 */}
            {assetCategory === 'video' && (
              <div className="space-y-3">
                {/* 上传区 */}
                <div
                  onClick={() => videoFileInputRef.current?.click()}
                  className={`w-full h-24 bg-slate-950/50 border-2 border-dashed rounded-xl flex flex-col items-center justify-center cursor-pointer transition-colors ${videoFileName ? 'border-violet-500 bg-violet-900/10' : 'border-slate-700 hover:border-violet-500/60'}`}
                >
                  {videoFileName ? (
                    <>
                      <Film className="w-6 h-6 text-violet-400 mb-1" />
                      <p className="text-xs text-violet-400 font-bold">✓ 视频已加载</p>
                      <p className="text-[10px] text-violet-600 mt-0.5 truncate max-w-[220px]">
                        {videoFileName}
                        {videoMeta && <span className="ml-1 opacity-70">({(videoMeta.size / 1048576).toFixed(1)} MB)</span>}
                      </p>
                    </>
                  ) : (
                    <>
                      <UploadCloud className="w-6 h-6 text-slate-500 mb-1" />
                      <p className="text-xs text-slate-300 font-bold">点击上传视频文件</p>
                      <p className="text-[10px] text-slate-600 mt-0.5">MP4 / MOV / WEBM · Stage B 帧采样</p>
                    </>
                  )}
                </div>
                <input
                  ref={videoFileInputRef}
                  type="file"
                  accept="video/mp4,video/quicktime,video/webm,video/avi,video/*"
                  className="hidden"
                  onChange={handleVideoFile}
                />

                {/* 视频描述 */}
                <div>
                  <p className="text-[10px] text-slate-500 mb-1.5 flex items-center gap-1">
                    <Tag className="w-3 h-3" />
                    描述视频内容/场景 <span className="text-violet-400">(必填·Stage A 代理 + Stage B 帧采样双通道)</span>
                  </p>
                  <textarea
                    value={inputText}
                    onChange={(e) => setInputText(e.target.value)}
                    placeholder={"例: 4K电影级短片，专业摄影机，LOG调色，城市夜景空镜...\n或: 医疗手术纪录片 / 高校公开课讲解 / 日常 vlog 记录..."}
                    className="w-full h-20 bg-slate-950/50 border border-slate-700 rounded-xl p-3 font-mono text-xs text-violet-400 placeholder-slate-600 focus:outline-none focus:border-violet-500 resize-none"
                  />
                </div>

                {/* Stage 说明 */}
                <div className="text-[10px] text-slate-600 bg-slate-950/50 rounded-lg px-3 py-2 border border-slate-800/60 font-mono leading-relaxed">
                  <span className="text-violet-700">●</span> Stage B (有视频): OpenCV 均匀采样16帧 → CLIP ViT-B/32 → 时域多样性 + 镜头切换密度<br/>
                  <span className="text-slate-700">●</span> Stage A (仅描述): 文字代理推断，精度受限，适合快速预估<br/>
                  <span className="text-slate-600">  TEV 基础倍率: 500× · 场景权重: 图像分类器代理</span>
                </div>
              </div>
            )}

            {/* ★ v4 音频输入区 */}
            {assetCategory === 'audio' && (
              <div className="space-y-3">
                {/* 波形 / 状态区 */}
                <div className="bg-slate-950/70 border border-slate-800 rounded-xl p-3 space-y-2">
                  <div className="flex items-center justify-between text-[10px] font-mono">
                    <span className={isRecording ? 'text-red-400 flex items-center gap-1' : 'text-slate-500'}>
                      {isRecording && <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse inline-block" />}
                      {isRecording ? `录音中 ${recSeconds}s` : audioB64 ? '音频就绪' : '等待音频输入'}
                    </span>
                    {audioFileName && <span className="text-slate-400 truncate max-w-[140px]">{audioFileName}</span>}
                    {audioB64 && !audioFileName && <span className="text-emerald-400">✓ 波形已捕获</span>}
                  </div>

                  {/* 波形画布 */}
                  <WaveformCanvas analyserRef={analyserRef} isRecording={isRecording} />

                  {/* 未录音时的静态占位 */}
                  {!isRecording && !audioB64 && (
                    <div className="text-center text-[10px] text-slate-600 -mt-1 pb-1">
                      声学特征将由后端 AudioAdapter 提取 (ZCR / HNR / chroma_var / beat)
                    </div>
                  )}
                </div>

                {/* 录音 / 上传 按钮行 */}
                <div className="flex gap-2">
                  {!isRecording ? (
                    <button
                      onClick={startRecording}
                      className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-xs font-bold bg-emerald-900/30 border border-emerald-500/40 text-emerald-400 hover:bg-emerald-900/50 transition-colors"
                    >
                      <Mic className="w-4 h-4" /> 开始录音
                    </button>
                  ) : (
                    <button
                      onClick={stopRecording}
                      className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-xs font-bold bg-red-900/30 border border-red-500/40 text-red-400 hover:bg-red-900/50 transition-colors"
                    >
                      <StopCircle className="w-4 h-4" /> 停止录音
                    </button>
                  )}
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-xs font-bold bg-slate-800/60 border border-slate-700 text-slate-400 hover:text-slate-300 transition-colors"
                  >
                    <UploadCloud className="w-4 h-4" /> 上传音频
                  </button>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="audio/*"
                    className="hidden"
                    onChange={handleAudioFile}
                  />
                </div>

                {/* 音频描述文本 */}
                <div>
                  <p className="text-[10px] text-slate-500 mb-1.5 flex items-center gap-1">
                    <Tag className="w-3 h-3" />
                    描述音频内容/场景 <span className="text-emerald-400">(文本通道 ×0.35，辅助细粒度场景分类)</span>
                  </p>
                  <textarea
                    value={inputText}
                    onChange={(e) => setInputText(e.target.value)}
                    placeholder={"例: 医院临床访谈录音，医生诊断陈述，包含专业术语...\n或: 庭审证词录音 / 原创钢琴曲 / 环境音效包..."}
                    className="w-full h-20 bg-slate-950/50 border border-slate-700 rounded-xl p-3 font-mono text-xs text-emerald-400 placeholder-slate-600 focus:outline-none focus:border-emerald-500 resize-none"
                  />
                </div>

                {/* 双通道说明 */}
                <div className="text-[10px] text-slate-600 bg-slate-950/50 rounded-lg px-3 py-2 border border-slate-800/60 font-mono leading-relaxed">
                  <span className="text-emerald-700">●</span> 声学通道 ×0.65：ZCR / HNR / chroma_var / beat_str → 语音/音乐/噪声<br/>
                  <span className="text-blue-700">●</span> 文本通道 ×0.35：关键词密度 → 医疗/法律/教育子类细分<br/>
                  <span className="text-slate-600">  method = fusion | acoustic | text_proxy</span>
                </div>
              </div>
            )}

            {/* 调试 · 场景覆盖 */}
            <div className="rounded-xl border border-slate-800 overflow-hidden">
              <button
                onClick={() => setShowDebug(!showDebug)}
                className={`w-full flex items-center justify-between px-3.5 py-2.5 text-xs font-mono transition-colors ${showDebug ? 'bg-slate-800/80 text-slate-300' : 'bg-slate-950/50 text-slate-500 hover:text-slate-400'}`}
              >
                <span className="flex items-center gap-2">
                  <FlaskConical className={`w-3.5 h-3.5 ${isOverrideActive ? 'text-amber-400' : 'text-slate-500'}`} />
                  调试 · 场景覆盖
                  {isOverrideActive && (
                    <span className="px-1.5 py-0.5 rounded bg-amber-900/40 border border-amber-500/40 text-amber-300 text-[9px] font-bold tracking-wider">
                      已覆盖: {sceneOverride}
                    </span>
                  )}
                </span>
                {showDebug ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
              </button>

              {showDebug && (
                <div className="p-3 bg-slate-950/70 border-t border-slate-800 space-y-2">
                  <div className="flex items-center justify-between">
                    <p className="text-[10px] text-slate-500">
                      强制指定场景类型，绕过 SceneClassifier v4 双通道推理。用于测试音频细粒度定价路径。
                    </p>
                    {scenesLoaded ? (
                      <span className="ml-2 flex-shrink-0 px-1.5 py-0.5 rounded text-[9px] font-mono bg-emerald-900/50 text-emerald-400 border border-emerald-700/40">
                        ⚡ 动态
                      </span>
                    ) : (
                      <span className="ml-2 flex-shrink-0 px-1.5 py-0.5 rounded text-[9px] font-mono bg-slate-800 text-slate-500 border border-slate-700">
                        静态兜底
                      </span>
                    )}
                  </div>
                  <select
                    value={sceneOverride}
                    onChange={(e) => setSceneOverride(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs font-mono text-slate-300 focus:outline-none focus:border-purple-500"
                  >
                    {/* 按 group 分组展示 */}
                    <optgroup label="── 自动 ──">
                      {sceneOptions.filter(o => o.group === 'auto').map(o =>
                        <option key={o.value} value={o.value}>{o.label}</option>)}
                    </optgroup>
                    <optgroup label="── 文本场景 ──">
                      {sceneOptions.filter(o => o.group === 'text').map(o =>
                        <option key={o.value} value={o.value}>{o.label}</option>)}
                    </optgroup>
                    <optgroup label="── 图像场景 ──">
                      {sceneOptions.filter(o => o.group === 'image').map(o =>
                        <option key={o.value} value={o.value}>{o.label}</option>)}
                    </optgroup>
                    <optgroup label="── 音频细粒度场景 (v4) ──">
                      {sceneOptions.filter(o => o.group === 'audio').map(o =>
                        <option key={o.value} value={o.value}>{o.label}</option>)}
                    </optgroup>
                    <optgroup label="── 视频场景 (v5) ──">
                      {sceneOptions.filter(o => o.group === 'video').map(o =>
                        <option key={o.value} value={o.value}>{o.label}</option>)}
                    </optgroup>
                  </select>
                  {isOverrideActive && (
                    <p className="text-[10px] text-amber-400 flex items-center gap-1">
                      ⚠ 场景覆盖已激活，将跳过 SceneClassifier v4 的 dual-channel fusion 推理
                    </p>
                  )}
                  {/* Stage C 双流推理状态徽标 */}
                  {assetCategory === 'video' && dualStreamInfo && (
                    <div className="mt-1 p-2 rounded-lg bg-purple-950/40 border border-purple-700/30 text-[10px] font-mono">
                      <div className="flex items-center gap-1.5 text-purple-300 font-semibold mb-0.5">
                        <span>🎬</span>
                        <span>VideoAdapter Stage {dualStreamInfo.stage} 双流推理</span>
                        {dualStreamInfo.ffmpeg_available
                          ? <span className="ml-auto text-emerald-400">ffmpeg ✓</span>
                          : <span className="ml-auto text-amber-400">ffmpeg 未安装 (纯视觉)</span>
                        }
                      </div>
                      {dualStreamInfo.ffmpeg_available && (
                        <div className="text-slate-400">
                          视觉流 α={dualStreamInfo.fusion_alpha} · 音频流 β={dualStreamInfo.fusion_beta}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>

            <button
              onClick={processData}
              disabled={isProcessing}
              className={`w-full py-3.5 font-black rounded-xl transition-all shadow-lg text-white text-sm
                ${assetCategory === 'audio'
                  ? 'bg-gradient-to-r from-emerald-600 to-teal-500 hover:from-emerald-700 hover:to-teal-600'
                  : assetCategory === 'video'
                  ? 'bg-gradient-to-r from-violet-600 to-purple-500 hover:from-violet-700 hover:to-purple-600'
                  : enableZK
                    ? 'bg-gradient-to-r from-purple-600 to-indigo-500 hover:from-purple-700 hover:to-indigo-600'
                    : 'bg-slate-700 hover:bg-slate-600'}
                ${isProcessing ? 'opacity-60 cursor-not-allowed' : ''}`}
            >
              {isProcessing ? '底层适配器执行中...' : '启动多模态质量甄别引擎'}
            </button>
          </div>

          {/* RIGHT: Demo presets + log */}
          <div className="space-y-4">
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
                    <span className={`ml-2 text-[10px] ${presetColor(preset)}`}>
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
