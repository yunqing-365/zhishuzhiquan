# 知数知圈 · 升级日志

> 基于质量审计 SVG（65% 数据流完整性）的系统性升级，共 8 轮。
> 所有文件路径均相对于项目根目录 `zhishuzhiquan/`。

---

## 文件归位总表

| 文件 | 操作 | 所属轮次 | 关键变更 |
|------|------|----------|---------|
| `ai-echo-backend/store/__init__.py` | **新建** | 第1轮 | 持久化模块入口 |
| `ai-echo-backend/store/db.py` | **新建** | 第1轮→第6轮 | SQLite 样本/包元数据持久层（账本双写冲突已消除） |
| `ai-echo-backend/dataset/pipeline.py` | **替换** | 第1轮→第4轮→第5轮 | v3：监控埋点、并发锁、SQLite、Parquet、安全预过滤、版本快照 |
| `ai-echo-backend/dataset/packager.py` | **替换** | 第1轮 | 追加 `ParquetExporter` + HuggingFace DataCard |
| `ai-echo-backend/dataset/versioning.py` | **替换** | 第3轮 | v2：JSON→SQLite，`snapshot_from_package()` 自动快照，版本 diff |
| `ai-echo-backend/dataset/human_review.py` | 已有 | — | 已迁 SQLite，无需修改 |
| `ai-echo-backend/dataset_api.py` | **替换** | 第1轮→第3轮→第5轮→第6轮 | 账本端点对齐 CreatorLedger、SSE 修复、版本端点、settle 端点 |
| `ai-echo-backend/requirements.txt` | **替换** | 第8轮 | 去重、加 pyarrow、整理可选依赖 |
| `.env.example` | **替换** | 第8轮 | 补全 OPENAI_* / JWT / 路径等所有必要变量 |
| `src/api.js` | **替换** | 第2轮→第3轮 | 补 balance/ledger/monitor/versions/settle 方法；修 detectCollision |
| `src/CreatorDashboard.jsx` | **替换** | 第2轮→第3轮 | 账本 Tab、监控 Tab、版本历史区块；接入 SQLite 余额 |
| `src/DatasetCatalog.jsx` | **替换** | 第2轮 | 包卡片 Parquet ✓ 标签 |
| `src/DatasetProductionScreen.jsx` | **替换** | 第3轮 | 完全重写：移除无效 apiClient 调用，SSE+轮询双保险，实时进度数字 |
| `src/BatchUploadPanel.jsx` | **替换** | 第5轮 | XHR 进度条；安全拦截/格式错误分类展示 |
| `src/DataInputScreen.jsx` | **替换** | 第7轮 | 接收 `onMaterialUploaded`，处理完成后调 `/api/dataset/ingest` 获取 materialId |
| `src/SmartSplitScreen.jsx` | **替换** | 第7轮 | 接收 `datasetPackage` prop，展示数据集溯源卡 |
| `src/AnalyticsDashboard.jsx` | **替换** | 第8轮 | 新增「数据集」Tab：样本分布、类型分布条、版本快照列表 |

---

## 各轮升级说明

### 第 1 轮 — P0/P1/P2 基础设施
- **`store/db.py`**（新建）：SQLite 持久层，覆盖 SFT/DPO/Pretrain 样本、包元数据
- **`pipeline.py` v3**：监控埋点（`PipelineMonitor`）、`asyncio.Lock` 并发写保护、SQLite 批量写入
- **`packager.py`**：追加 `ParquetExporter`，生成 `.parquet`（Snappy）+ `dataset_info.json` + README YAML front-matter
- **`dataset_api.py`**：新增 `/api/creator/balance`、`/api/creator/ledger`、`/api/platform/stats`、`/api/dataset/packages`

### 第 2 轮 — 前端接入新端点
- **`api.js`**：补 `myBalance()`、`myLedger()`、`monitorSnapshot()`、`alerts()`、`listPackagesSqlite()`
- **`CreatorDashboard.jsx`**：账本流水 Tab、监控告警 Tab；收益卡接入 SQLite 余额显示
- **`DatasetCatalog.jsx`**：包卡片新增 `Parquet ✓` 紫色标签

### 第 3 轮 — 阻断级 Bug 修复
- **`DatasetProductionScreen.jsx`**（完全重写）：
  - 移除无效 `apiClient.post/.get/.baseUrl` 调用
  - SSE 订阅 `/api/dataset/job/{id}/stream` + 轮询降级双保险
  - 实时显示已标注/已质检/已去重/已打包四个数字
- **`dataset_api.py`**：修正 `stream_url` 指向真实 SSE 端点；新增 SSE 端点
- **`versioning.py` v2**：JSON→SQLite，`snapshot_from_package()` 自动快照，版本 diff
- **`pipeline.py`**：打包后自动调用 `version_manager.snapshot_from_package()`
- **`api.js`**：补 `listVersions()`、`versionDiff()`；修 `detectCollision` 无效调用

### 第 4 轮 — 账本双写冲突消除
- **`pipeline.py`**：Stage 5 分润统一走 `CreatorLedger.add_records()`，移除 `store.db` 双写

### 第 5 轮 — 安全预过滤 + 批量上传升级
- **`pipeline.py`**：Stage 0 内容安全预过滤，素材进流水线前批量审核
- **`BatchUploadPanel.jsx`**（完全重写）：
  - `XMLHttpRequest` 替换 `fetch`，实现实时上传进度条
  - 成功结果展示「安全拦截」数字卡片
  - 错误明细分类：安全拦截（橙色）vs 格式解析错误（黄色），折叠展示

### 第 6 轮 — 账本数据源统一
- **`dataset_api.py`**：
  - `/api/creator/balance`、`/api/creator/ledger` 改从 `CreatorLedger` 读取（实际写入点）
  - `/api/platform/stats` 收益数据改从 `CreatorLedger.get_all_balances()` 汇总
  - 新增 `/api/creator/settle` 结算触发端点
- **`store/db.py`**：移除死表（`revenue_records`、`creator_ledger`）和对应死索引

### 第 7 轮 — 主流程路由断路修复
- **`DataInputScreen.jsx`**：
  - 接收 `onMaterialUploaded` prop（原来完全不接收）
  - `processData` 动画完成后调 `/api/dataset/ingest` 获取 `material_id`
  - 降级策略：未登录或 ingest 失败 → 走旧路径直接估值
- **`SmartSplitScreen.jsx`**：接收 `datasetPackage` prop，展示数据集溯源卡（包名、样本数、均质分、SFT/DPO 拆分、Parquet 标记）

### 第 8 轮 — 可运行性收尾
- **`.env.example`**（完全重写）：补全所有必要变量，含 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`、`JWT_SECRET`、所有路径变量
- **`requirements.txt`**（完全重写）：去除重复 `chromadb`，`pyarrow` 归位，整理可选依赖注释
- **`AnalyticsDashboard.jsx`**：新增「数据集」第五个 Tab，展示 SFT/DPO/PT 样本分布、版本快照列表

---

## 快速启动

```bash
# 1. 克隆并配置环境变量
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY 和 JWT_SECRET

# 2. 本地开发
cd ai-echo-backend
pip install -r requirements.txt
uvicorn app:app --reload --port 8000

# 另开终端
npm install
npm run dev   # Vite 代理自动转发 /api → localhost:8000

# 3. Docker 生产
docker-compose up -d --build
# 访问 http://localhost (或 FRONTEND_PORT 指定的端口)
```

---

## 关键架构决策

| 问题 | 决策 |
|------|------|
| 账本双写 | 唯一真实账本 = `CreatorLedger`（`ai-echo-backend/data/creator_ledger.db`），`store.db` 只存样本和包元数据 |
| 版本快照 | `pipeline.py` 打包后自动调 `version_manager.snapshot_from_package()`，写入 `store.db` 的 `dataset_versions` 表 |
| SSE 进度 | `/api/dataset/job/{id}/stream`（真实端点），降级为 2s 轮询 `/api/dataset/job/{id}` |
| Parquet 输出 | 可选（`pyarrow` 未安装时跳过），不阻断主流程 |
| 安全审核 | 三层：关键词 → 启发式规则 → LLM；`content_safety` 不可用时放行（不阻断） |
