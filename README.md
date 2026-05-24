# 指数之源 · AI-Echo Protocol

> 多模态数据集生产系统 + 链上产权定价协议

## 业务链路

```
创作者上传素材（文本/图像/音频/视频）
       ↓
[Step 1] 素材录入         DataInputScreen
       ↓
[Step 2] 数据集自动生产   DatasetProductionScreen
         ├─ LLM 自动标注（SFT / DPO / Pretrain / 多模态）
         ├─ 七维质检评分（铂金/黄金/白银分档）
         ├─ 三级去重（哈希→MinHash→向量语义）
         └─ 多格式打包（JSONL + Parquet + data_card + ZIP）
       ↓
[Step 3] 预言机估值       OracleValuationScreen
         ├─ TEV 多模态复合评分
         ├─ KNN-Shapley 公平贡献度
         ├─ AMM 联合曲线动态定价
         └─ ZK Poseidon 链上确权
       ↓
[Step 4] 合约分账         SmartSplitScreen
         ├─ 企业客户购买数据集
         └─ 平台30% / 创作者70% 智能分润
```

## 系统架构

```
前端 (React 19 + Vite)
  ↓ /api/* 代理
后端 (FastAPI + ChromaDB)  ←→  oracle_engine v7
  ├── dataset/                   数据集生产子系统
  │   ├── annotator.py           LLM 自动标注
  │   ├── quality_scorer.py      七维质检
  │   ├── deduplicator.py        三级去重
  │   ├── packager.py            多格式打包
  │   └── pipeline.py            端到端调度
  ├── creator/
  │   └── revenue_calculator.py  创作者分润引擎
  ├── dataset_api.py             数据集 REST API
  └── config.py                  统一配置管理
  ↓
智能合约 (Solidity 0.8.20)  ←→  AI_Echo_Contracts v5.0
```

## 快速启动

```bash
# Mac/Linux
bash dev.sh

# Windows
dev.bat
```

### 手动启动

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY（数据集标注引擎需要）

# 2. 启动后端
cd ai-echo-backend
pip install -r requirements.txt
python -m uvicorn oracle_engine:app --host 0.0.0.0 --port 8000 --reload

# 3. 启动前端（新开终端）
npm install
npm run dev
```

浏览器访问 http://localhost:5173

## 核心 API

### 数据集生产

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/dataset/ingest`        | POST | 上传创作者素材 |
| `/api/dataset/produce`       | POST | 启动生产任务（后台异步） |
| `/api/dataset/job/{id}/stream` | GET  | SSE 实时进度流 |
| `/api/dataset/packages`      | GET  | 列出已生产数据集包 |
| `/api/dataset/sell`          | POST | 记录销售 → 触发分润 |
| `/api/creator/{id}/earnings` | GET  | 创作者收益查询 |
| `/api/platform/stats`        | GET  | 平台整体统计 |

### 预言机估值（原有）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/valuate`         | POST | 多模态资产估值 |
| `/api/detect_collision`| POST | 相似度碰撞检测 |
| `/api/batch_valuate`   | POST | 批量估值（最多20条）|
| `/api/history`         | GET  | 估值历史记录 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAI_API_KEY`        | 必填   | 标注 LLM 密钥 |
| `OPENAI_BASE_URL`       | OpenAI | 可替换为国产模型接口 |
| `OPENAI_MODEL`          | `gpt-4o-mini` | 标注模型 |
| `PLATFORM_REVENUE_RATIO`| `0.30` | 平台留成比例 |
| `VITE_API_URL`          | `http://localhost:8000` | 后端地址 |
| `ALLOWED_ORIGINS`       | 本地   | 跨域白名单 |

## 数据存储

```
ai-echo-backend/data/
├── history.db          # SQLite：估值历史 + 数据集任务 + 分润记录
├── chroma_db/          # ChromaDB：资产向量库（碰撞检测用）
├── datasets/           # 打包输出目录（JSONL/Parquet/ZIP）
├── pipeline_state/     # 生产任务断点状态（重启可恢复）
└── creator_ledger.json # 创作者账本持久化
```
