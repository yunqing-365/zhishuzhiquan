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

---

## 第 9 轮 — P0 扫尾 + P3 企业买家市场

### 变更文件

| 文件 | 操作 | 关键变更 |
|------|------|---------|
| `.github/workflows/ci.yml` | **新建** | Backend pytest + Frontend lint/build + Hardhat 合约测试，触发 push/PR |
| `.github/workflows/cd.yml` | **新建** | Docker 多镜像构建推送 GHCR，支持 tag 触发和手动 staging 部署 |
| `ai-echo-backend/dataset/db.py` | **删除** | 移除第6轮遗留的僵尸文件（store/db.py 旧副本），防止误 import |
| `ai-echo-backend/dataset_api.py` | **追加** | 新增 `/api/market/packages`（多维筛选+排序+脱敏）、`/api/market/package/{id}`（详情+样本预览）、`/api/market/stats`（首页统计）三个公开端点 |
| `ai-echo-backend/store/db.py` | **追加** | 新增 `get_package()`、`list_samples_by_package()` 两个辅助函数 |
| `src/DatasetMarketplace.jsx` | **新建** | 企业买家市场全页面组件：统计横幅、筛选侧边栏、多维卡片网格、详情抽屉（含样本预览）、购买流程 |
| `src/api.js` | **追加** | 新增 `marketClient`：`listPackages()`、`getPackage()`、`getStats()`、`purchase()`、`download()` |
| `src/App.jsx` | **替换** | 引入 `DatasetMarketplace`；顶栏新增「市场」入口（`ShoppingBag`）区分买家/创作者视图 |
| `src/DataInputScreen.jsx` | **替换** | 首页新增「数据集市场」快捷入口按钮，接收 `onMarketplace` prop |

### 架构说明

**创作者视图 vs 买家视图分离**

| 维度 | DatasetCatalog（我的包） | DatasetMarketplace（市场） |
|------|------------------------|--------------------------|
| 入口 | 顶栏「我的包」按钮 | 顶栏「市场」+ 首页快捷按钮 |
| 登录 | 需要（JWT） | 无需（公开浏览） |
| API | `/api/dataset/packages`（私有） | `/api/market/packages`（公开） |
| 数据 | 返回 export_paths、creator_id | 脱敏（去除路径和用户ID） |
| 功能 | 版本管理、下载、结算 | 搜索、筛选、预览、购买 |

**市场端点筛选维度**：关键词 / 领域（7个）/ 类型（SFT/DPO/预训练）/ 最低质量分 / 最高价格 / 排序（质量/样本量/价格/最新）

**样本预览安全策略**：
- 指令/输出截断（120/200字符），防止全量数据泄露
- 创作者ID不暴露（只返回贡献者数量）
- export_paths（下载路径）仅购买后通过下载接口返回

---

## 第 10 轮 — 真正的多厂商模型投票

> 修复 P1 遗留问题：MultiModelAnnotator 声称支持跨厂商多模型，但 `_llm_call`
> 始终使用主模型的 `OPENAI_BASE_URL`，副模型无论配了 DeepSeek / Moonshot 哪家
> 的地址都不生效，实际上只有 temperature 不同的伪多模型。

### 变更文件

| 文件 | 关键变更 |
|------|---------|
| `ai-echo-backend/config.py` | 新增 `openai_base_url_b` 字段，从 `OPENAI_BASE_URL_B` 环境变量读取 |
| `ai-echo-backend/dataset/annotator.py` | 读取 `_BASE_URL_B`；`_llm_call` 增加 `base_url` 参数；SFT/DPO 副模型调用传入 `base_url=_BASE_URL_B` |
| `.env.example` | 新增 `OPENAI_BASE_URL_B` 配置项，含 DeepSeek / Moonshot / Azure 示例 |
| `docker-compose.yml` | 新增 `OPENAI_MODEL_B` + `OPENAI_BASE_URL_B` 环境变量透传 |

### 配置方式

```dotenv
# .env 示例：OpenAI 主模型 + DeepSeek 副模型
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=https://api.openai.com/v1

OPENAI_MODEL_B=deepseek-chat
OPENAI_BASE_URL_B=https://api.deepseek.com/v1
```

副模型调用链：`MultiModelAnnotator.annotate_sft/dpo`
→ `_llm_call(..., model=_MODEL_B, base_url=_BASE_URL_B)`
→ `POST {OPENAI_BASE_URL_B}/chat/completions`

两者都配置时为真正的跨厂商投票；只配 `OPENAI_MODEL_B` 不配 `BASE_URL_B`
时降级为同地址不同模型（如同一 Azure 部署下的两个模型）；
均不配时维持原行为（temperature 差异）。

---

## 第 11 轮 — 样本预览链路修通（package_id 回填）

> 根因：`sft_samples` / `dpo_samples` 表没有 `package_id` 列，
> `list_samples_by_package()` 的 WHERE 查询始终返回空，导致市场详情页样本预览
> 永远显示"购买后可见"，即使数据集里有几千条样本。

### 变更文件

| 文件 | 关键变更 |
|------|---------|
| `ai-echo-backend/dataset/schema.py` | `SFTSample` + `DPOSample` 新增 `package_id: str = ""` 字段 |
| `ai-echo-backend/store/db.py` | 建表 DDL 加 `package_id TEXT DEFAULT ''`；`init_db()` 增加在线迁移（ALTER TABLE ADD COLUMN，幂等）；`bulk_insert_sft/dpo` 写入时携带 `package_id`；补充 `idx_sft_package` / `idx_dpo_package` 索引 |
| `ai-echo-backend/dataset/pipeline.py` | Stage 4 打包后、`save_package` 前，新增 `_backfill_pkg_id()` 异步 UPDATE：将确定的 `package.package_id` 写回已入库的 SFT/DPO 样本行 |

### 完整链路

```
pipeline.py Stage 2
  → bulk_insert_sft(sft_samples)   # 此时 package_id=""（还没打包）

pipeline.py Stage 4
  → packager.pack(...)             # 生成 package.package_id
  → _backfill_pkg_id()             # UPDATE sft_samples SET package_id=? WHERE sample_id=?
  → save_package(package)          # 包元数据入库

dataset_api.py /api/market/package/{id}
  → store.db.list_samples_by_package(package_id, limit=3)
  → SELECT ... FROM sft_samples WHERE package_id = ?   ✅ 现在能查到数据
  → 返回脱敏预览给买家
```

### 向后兼容

旧库（已有数据，无 package_id 列）：`init_db()` 会执行 `ALTER TABLE ADD COLUMN`，
列默认值为空字符串，旧样本 `package_id=""` 不影响已有功能，仅新生产的数据集才有预览。

---

## 第 12 轮 — buyer_id 一致性修复 + aiohttp 依赖补全 + 密码强度

### 三个独立 Bug，一轮修清

---

### Bug 1：购买→下载 buyer_id 不一致，下载永远 403

**根因**：
- `marketClient.purchase()` 每次生成 `'buyer_' + Date.now()`（随机）
- `DatasetMarketplace.handleBuy(isDownload=true)` 用 `tokenStore.get()`（JWT token，完全不同字符串）
- 下载端点 `check_purchase(buyer_id, package_id)` 查不到购买记录 → 403

**修复**（`src/api.js` + `src/DatasetMarketplace.jsx`）：
- 新增 `getBuyerId()` 函数：优先取登录用户的 `creator_id`，否则生成一次后持久化到 `sessionStorage['zszq_buyer_id']`，同一 tab 内值永不变
- `marketClient.purchase()` / `download()` 统一调用 `getBuyerId()`
- `DatasetMarketplace.handleBuy` 移除手动 buyerId，调用无参 `marketClient.download(pkg.package_id)`

---

### Bug 2：aiohttp 未安装，LLM 标注/评分静默失败

**根因**：`annotator.py` / `quality_scorer.py` 在 `_llm_call` 中 `import aiohttp`，
但 `requirements.txt` 中缺少此依赖，`try: import aiohttp except: return ""` 静默降级，
导致所有样本走规则标注，质量分不准确，且无任何错误日志。

**修复**（`ai-echo-backend/requirements.txt`）：
```
aiohttp==3.10.11   # AutoAnnotator / QualityScorer 异步调用 LLM API（必须）
```

---

### Bug 3：密码强度前后端不对齐

**根因**：后端 `auth.py` 只检查 `len >= 6`；前端 `AuthPanel.jsx` 无强度 UI；
用户可以设置 `"123456"` 这类极弱密码。

**修复**：
- `ai-echo-backend/auth.py`：密码规则升级为 `≥8位 + 大写/数字/符号至少两种`
- `src/AuthPanel.jsx`：
  - 新增 `calcPasswordStrength()` + `PasswordStrengthMeter` 组件（5段彩色进度条 + 4个规则指示灯）
  - 前端校验同步为 `≥8位` 规则
  - 强度提示：弱→一般→中等→强→极强

| 文件 | 变更 |
|------|------|
| `src/api.js` | 新增 `getBuyerId()`、`BUYER_KEY`；`marketClient.purchase/download` 统一用 `getBuyerId()` |
| `src/DatasetMarketplace.jsx` | `handleBuy` 简化，移除错误的 `tokenStore.get()` |
| `ai-echo-backend/requirements.txt` | 新增 `aiohttp==3.10.11` |
| `ai-echo-backend/auth.py` | 密码规则：≥8位 + 复杂度≥2 |
| `src/AuthPanel.jsx` | 密码强度进度条组件 + 校验对齐后端规则 |

---

## 第 13 轮 — 安全加固：sell 端点防滥用 + 合约 ReentrancyGuard

### 变更文件

| 文件 | 关键变更 |
|------|---------|
| `ai-echo-backend/dataset_api.py` | `SellRequest` 增加 `price_cny` 范围校验（≥0, ≤100万, 有限值）；`sell_dataset` 增加可选JWT鉴权 + 价格合理性校验 |
| `contracts/AIEchoProtocol.sol` | 内联 `ReentrancyGuard`；`purchaseAndCallData` 改 CEI 模式；`transfer` 改 `call{value}` |

---

### Bug 1：`/api/dataset/sell` 无鉴权且 price_cny 无校验

**风险**：任何人可向该端点 POST 任意 `price_cny`（如 999999），触发虚假高额分润写入创作者账本，或以 price_cny=0 "免费"获取购买记录绕过下载鉴权。

**修复**：

```python
# SellRequest 新增 validator
price_cny: float = Field(..., ge=0, le=1_000_000)

@field_validator("price_cny")
def price_non_negative_and_finite(cls, v):
    if not math.isfinite(v): raise ValueError("价格必须是有限数值")
    return round(v, 2)
```

```python
# sell_dataset 新增可选鉴权 + 价格上限
async def sell_dataset(req, caller=Depends(get_optional_creator)):
    if caller:
        req = req.model_copy(update={"buyer_id": caller["creator_id"]})  # 防伪造
    if pkg_price > 0 and req.price_cny > pkg_price * 2:
        raise HTTPException(422, "价格超过包定价2倍上限")
```

---

### Bug 2：Solidity `purchaseAndCallData` CEI 违规 + 无重入锁

**风险**：旧代码在颁发凭证（状态写入）**之后**才执行 `transfer`，若创作者地址是恶意合约，可在 `receive()` 中重入 `purchaseAndCallData`，以同一 `msg.value` 重复触发分账。

**修复 1 — 内联 ReentrancyGuard（无需 OZ 依赖）**：
```solidity
uint256 private _reentrancyStatus;
modifier nonReentrant() {
    require(_reentrancyStatus != _ENTERED, "ReentrancyGuard: reentrant call");
    _reentrancyStatus = _ENTERED;
    _;
    _reentrancyStatus = _NOT_ENTERED;
}
```

**修复 2 — CEI 顺序（Checks → Effects → Interactions）**：
```
旧顺序：[颁发凭证] → [transfer creator] → [transfer platform] → [emit]
                          ↑ 这里可以重入

新顺序：[所有 require] → [domainDemandLedger++] → [accessTokens写入] 
      → [计算金额] → [emit] → [call{value} creator] → [call{value} platform]
                                                         ↑ 状态已锁，重入无效
```

**修复 3 — `transfer` 改 `call{value}`**：
`transfer` 硬编码 2300 gas，EIP-1884 后对某些合约会 revert。改用 `call{value}("")` 并检查返回值。

---

## 第 14 轮 — 骨架屏 + 合约紧急暂停

### 变更文件

| 文件 | 关键变更 |
|------|---------|
| `src/DatasetCatalog.jsx` | 新增 `CardSkeleton` 组件；加载时展示 8 个骨架卡片网格替代转圈 Spinner |
| `contracts/AIEchoProtocol.sol` | 新增 `bool public paused`、`onlyAdmin`、`whenNotPaused` modifier；`registerAsset` + `purchaseAndCallData` 加 `whenNotPaused`；新增 `pause()`/`unpause()`/`transferAdmin()`/`acceptAdmin()` 函数 |

### DatasetCatalog 骨架屏

旧：加载时展示居中 Spinner，面积空旷、内容跳跃感强。  
新：展示 8 个与真实卡片等尺寸的 `CardSkeleton`（含头部占位、质量条、统计行、价格行），`animate-pulse` 闪烁。数据加载完毕后直接替换为真实卡片，布局不偏移。

### 合约紧急暂停

**场景**：发现合约逻辑漏洞、价格预言机异常或前端遭受攻击，需在几分钟内阻止新购买和新注册，争取修复时间。

```solidity
// 暂停后 registerAsset + purchaseAndCallData 均返回错误
function pause()   external onlyAdmin { paused = true;  emit ContractPaused(...); }
function unpause() external onlyAdmin { paused = false; emit ContractUnpaused(...); }
```

**管理员权限两步交接**（防止误转移锁死）：
```
1. platformAdmin 调 transferAdmin(newAddr) → pendingAdmin = newAddr
2. newAddr 调 acceptAdmin() → platformAdmin = newAddr
```
只读查询（`getAccessToken`、`getDomainAlpha` 等）不受暂停影响，买家仍可查看数据。

---

## 第 15 轮 — CI/CD 补全 + 人工复核面板 + 合约管理员控制台

### 三个遗留缺口，一轮补清

---

### 缺口 1：CI/CD 工作流文件从未真正落地

**根因**：UPGRADE_LOG 第9轮列出了 `.github/workflows/ci.yml` 和 `cd.yml`，
但仓库里这两个文件根本不存在，任何 push 都不会触发自动化流水线。

**修复**：

| 文件 | 操作 | 内容 |
|------|------|------|
| `.github/workflows/ci.yml` | **新建** | Backend pytest + Frontend ESLint/build + Hardhat 合约测试 + Docker 构建检查（不推送），触发 push/PR |
| `.github/workflows/cd.yml` | **新建** | 多架构（amd64/arm64）Docker 镜像构建并推送 GHCR；支持 tag 触发正式发布和手动 workflow_dispatch staging 部署；生产部署需经 staging 验证门 |

**CI 流程**：
```
push / PR
  ├─ backend-test      pytest + coverage report (codecov)
  ├─ frontend-build    eslint + vite build + dist artifact 上传
  ├─ contracts-test    hardhat compile + test
  └─ docker-build-check  构建两个镜像（不推送，验证 Dockerfile 可用）
```

**CD 触发规则**：
- `v*.*.*-rc*` tag → build & push → 自动部署 staging
- `v*.*.*`（非 rc/beta）tag → build & push → staging → 生产（需 environment 审批）→ 自动创建 GitHub Release
- `workflow_dispatch` → 手动选择 staging/production

---

### 缺口 2：人工复核面板 UI 缺失

**根因**：后端 `/api/review/queue`、`/api/review/{id}/approve`、
`/api/review/{id}/reject` 三个端点在第 3 轮已完备，但前端零 UI，
管理员无法看到流水线触发的人工复核样本，也无法做出批准/拒绝决策。
被拦截到 `review_queue` 表的低质量样本永远积压。

**修复**：

| 文件 | 操作 | 内容 |
|------|------|------|
| `src/HumanReviewPanel.jsx` | **新建** | 人工复核管理面板全组件 |
| `src/api.js` | **追加** | `reviewClient`：`list()` / `approve()` / `reject()` |
| `src/App.jsx` | **修改** | 引入 `HumanReviewPanel`；顶栏新增「复核」按钮（`ClipboardList` 图标） |

**HumanReviewPanel 功能**：
- 统计卡：待复核 / 已批准 / 已拒绝三格实时计数
- 类型筛选：全部 / SFT / DPO / PT / 多模态
- 每张卡片：类型标签 + 内容摘要 + 质量分（色阶）+ 拒绝原因 + 展开原始 JSON
- 单条操作：批准 / 拒绝，乐观更新 UI
- 批量操作：全选 + 批量批准/拒绝
- 已处理记录：折叠查看，可展开
- 10 秒轮询自动刷新，骨架屏加载态

---

### 缺口 3：合约管理员控制台 UI 缺失

**根因**：第 14 轮在合约里加了 `pause()/unpause()/transferAdmin()/acceptAdmin()`，
但前端 ABI 里没有这四个函数条目，也没有任何 UI 入口。
管理员发现漏洞时无法通过界面暂停合约，只能通过 Hardhat console 操作，
响应时间大幅延长。

**修复**：

| 文件 | 操作 | 内容 |
|------|------|------|
| `src/web3/ABI.js` | **补充** | 新增 `pause` / `unpause` / `transferAdmin` / `acceptAdmin` 函数 ABI；新增 `paused` / `pendingAdmin` view 函数 ABI；新增 `ContractPaused` / `ContractUnpaused` / `AdminTransferInitiated` / `AdminTransferAccepted` 事件 ABI |
| `src/AdminPanel.jsx` | **新建** | 合约管理员控制台全组件 |
| `src/App.jsx` | **修改** | 引入 `AdminPanel`；顶栏新增「管理」按钮（`Shield` 图标） |

**AdminPanel 功能**：
- 实时读取链上 `paused` / `platformAdmin` / `pendingAdmin` 状态（8s 轮询）
- 非管理员地址：只读模式，操作区半透明禁用
- 待接收管理员：醒目蓝色提示 + 一键接受
- 暂停 / 恢复：动态显示当前状态，按钮文案随状态反转
- 权限转移：地址格式校验 → `transferAdmin()` → 对方连接钱包 → `acceptAdmin()`
- 操作日志：当前会话内所有 tx hash（可点击跳转区块浏览器）

### 架构说明

**新入口在顶栏的位置**（从左到右）：
```
← 返回  [步骤进度]  市场  我的包  我的  复核  管理  [模态]  [后端心跳]  [登录状态]  [钱包]
```

复核面板和管理员面板均以全屏覆盖层展示（`fixed inset-0 z-50`），
右上角固定关闭按钮，不影响主流程步骤状态。

---

## 第 16 轮 — P0/P1 生产环境关键 Bug 修复

### 5 个独立 Bug，扫描发现，一轮修清

---

### Bug 1（P0）：docker-compose `dockerfile` 路径错误，`docker-compose up --build` 必报错

**根因**：`docker-compose.yml` 中两个 service 都写 `dockerfile: Dockerfile`，
但实际文件名是 `Dockerfile.backend`（后端）和 `Dockerfile.frontend`（前端），
执行 `docker-compose up --build` 时 Docker 找不到文件，直接报错退出。

**修复**：
```yaml
# 后端
dockerfile: Dockerfile.backend
# 前端
dockerfile: Dockerfile.frontend
```

---

### Bug 2（P0）：`apiFetch` 未导出，`HumanReviewPanel` 白屏

**根因**：第 15 轮新建的 `HumanReviewPanel.jsx` 中写了
`import { apiFetch } from './api'`，但 `api.js` 里 `apiFetch` 是
普通函数声明（`async function apiFetch`），没有 `export`。
Vite 构建时报 named export 找不到，前端白屏。

**修复**（`src/api.js`）：
```js
// 修复前
async function apiFetch(path, options = {}, timeoutMs = DEFAULT_TIMEOUT_MS)
// 修复后
export async function apiFetch(path, options = {}, timeoutMs = DEFAULT_TIMEOUT_MS)
```

---

### Bug 3（P1）：nginx `proxy_buffering` 未关闭，生产环境 SSE 进度流被缓冲

**根因**：`nginx.conf` 的 `/api/` location 块没有设置 `proxy_buffering off`，
nginx 默认开启响应缓冲，将 FastAPI `StreamingResponse`（SSE）的事件积压在缓冲区，
直到连接关闭才一次性 flush。用户在生产环境看不到实时进度，
`DatasetProductionScreen` 进度条一直卡在 0%，超时后才收到所有事件。

**修复**（`nginx.conf`）：
```nginx
location /api/ {
    proxy_buffering    off;           # 关闭响应缓冲，SSE 事件实时透传
    proxy_cache        off;
    proxy_set_header   X-Accel-Buffering no;   # 告知 FastAPI 禁用加速缓冲
    ...
}
```

---

### Bug 4（P1）：nginx `proxy_read_timeout 60s`，数据集生产任务超时断连

**根因**：`nginx.conf` 的 `/api/` location 块 `proxy_read_timeout 60s`，
数据集生产 pipeline（标注 → 质检 → 去重 → 打包）对于几千条样本可达 3-10 分钟，
60 秒后 nginx 强制断开 SSE 连接，前端降级为轮询但任务实际还在跑，
用户界面显示"连接断开"。

**修复**（`nginx.conf`）：
```nginx
proxy_read_timeout 600s;   # 10 分钟，覆盖最长生产任务
```

---

### Bug 5（P1）：`docker-compose.yml` 缺少 8 个关键环境变量透传

**根因**：`.env.example` 中有 `PLATFORM_REVENUE_RATIO`、`CREATOR_REVENUE_RATIO`、
`ENABLE_LLM_SAFETY`、`SQLITE_DB_PATH`、`CREATOR_LEDGER_DB_PATH`、
`DATASET_OUTPUT_DIR`、`PIPELINE_STATE_DIR`、`CHROMA_DB_DIR` 共 8 个变量，
但 `docker-compose.yml` 的 backend service 环境变量块中全部缺失。
用户在 `.env` 中修改这些值，`docker-compose up` 后容器里的 config.py
读不到，仍然使用代码硬编码默认值，运营参数调整完全不生效。

**修复**（`docker-compose.yml`）：补全所有 8 个变量透传，均配默认值降级：
```yaml
PLATFORM_REVENUE_RATIO: "${PLATFORM_REVENUE_RATIO:-0.30}"
CREATOR_REVENUE_RATIO:  "${CREATOR_REVENUE_RATIO:-0.70}"
ENABLE_LLM_SAFETY:      "${ENABLE_LLM_SAFETY:-false}"
SQLITE_DB_PATH:         "${SQLITE_DB_PATH:-}"
# ... 其余 4 个路径变量
```

| 文件 | 变更 |
|------|------|
| `docker-compose.yml` | 修复两个 `dockerfile` 路径；补全 8 个环境变量透传 |
| `nginx.conf` | `proxy_buffering off` + `X-Accel-Buffering no` + `proxy_read_timeout 600s` |
| `src/api.js` | `apiFetch` 加 `export`，修复 `HumanReviewPanel` 白屏 |
