# 息壤 · 数据集生产系统升级说明 v3.0

## 一、业务背景与目标

**业务模式：** 创作者上传原始素材 → 平台将素材转化为高质量AI训练数据集 → 卖给企业客户 → 利润按贡献比例分给创作者

**v3.0 核心新增能力：**

| 能力 | 模块 | 说明 |
|---|---|---|
| 多类型标注 | `dataset/annotator.py` | SFT / DPO / Pretrain / 多模态，LLM 自动生成 |
| 七维质检 | `dataset/quality_scorer.py` | 准确性/相关性/安全性等加权评分，自动分档 |
| 三级去重 | `dataset/deduplicator.py` | 哈希→MinHash→向量语义，三层递进 |
| 多格式打包 | `dataset/packager.py` | JSONL / Parquet / README / data_card / ZIP |
| 端到端调度 | `dataset/pipeline.py` | 五阶段流水线，断点续产，进度可查 |
| 创作者分润 | `creator/revenue_calculator.py` | 贡献权重计算，账本，结算，排行榜 |
| REST API | `dataset_api.py` | 12 个新端点，挂载到现有 server.py |

---

## 二、新增文件结构

```
xirang/
├── dataset/
│   ├── __init__.py
│   ├── schema.py          ← 核心数据模型（素材/SFT/DPO/Pretrain/多模态/包/分润）
│   ├── annotator.py       ← 自动标注引擎
│   ├── quality_scorer.py  ← 多维质检评分
│   ├── deduplicator.py    ← 三级去重流水线
│   ├── packager.py        ← 数据集打包导出
│   └── pipeline.py        ← 端到端生产调度器
├── creator/
│   ├── __init__.py
│   └── revenue_calculator.py  ← 分润计算 / 账本 / 分析
├── dataset_api.py         ← FastAPI 路由（挂载到 server.py）
└── data/
    ├── datasets/          ← 打包输出目录
    ├── pipeline_state/    ← 生产任务断点状态
    └── creator_ledger.json ← 创作者账本持久化
```

---

## 三、核心 API 端点

### 数据集生产

```
POST /api/dataset/ingest          上传创作者素材
POST /api/dataset/produce         启动生产任务（后台异步）
GET  /api/dataset/job/{job_id}    查询任务进度 + 各阶段耗时
GET  /api/dataset/jobs            列出最近50个任务
GET  /api/dataset/packages        列出已生产的数据集包
GET  /api/dataset/package/{id}    查看包详情
POST /api/dataset/sell            记录销售 → 触发分润计算
```

### 创作者

```
GET  /api/creator/{id}/balance    余额查询（pending / paid）
GET  /api/creator/{id}/records    分润明细
GET  /api/creator/{id}/report     完整贡献报告 + 月度趋势
POST /api/creator/{id}/settle     触发结算（≥¥10 才受理）
```

### 平台管理

```
GET  /api/platform/summary        汇总数据（创作者数/待结算/已结算）
GET  /api/platform/leaderboard    收益排行榜
```

---

## 四、生产流水线五阶段

```
创作者素材（CreatorMaterial）
  ↓
[Stage 1] 标注（annotator.py）
  LLM 并发生成 SFT / DPO / Pretrain / 多模态样本
  置信度 < 0.75 → 推人工复核队列
  ↓
[Stage 2] 质检（quality_scorer.py）
  七维加权评分（准确/相关/完整/可读/安全/多样/指令遵循）
  安全维度=0 → 直接丢弃（硬过滤）
  9+ 铂金 / 7+ 黄金 / 5+ 白银 / <5 丢弃
  ↓
[Stage 3] 去重（deduplicator.py）
  L1: SHA-256 精确哈希（毫秒级）
  L2: MinHash Jaccard > 0.85（秒级）
  L3: 向量余弦 > 0.95（仅黄金以上，API调用）
  ↓
[Stage 4] 打包（packager.py）
  JSONL（ShareGPT + Alpaca 双格式）
  Parquet（可选，需 pandas）
  README.md + data_card.json
  ZIP 压缩包（企业交付）
  ↓
[Stage 5] 分润（revenue_calculator.py）
  贡献权重 = 样本类型权重 × 质量加成 / 总权重
    SFT×1.5 / DPO×2.0 / Pretrain×0.5（按 token）
    铂金×2.0 / 黄金×1.5 / 白银×1.0
  创作者池 70%，平台留存 30%
  按贡献比例分配，写入账本，可审计
```

---

## 五、分润公式详解

```python
# 贡献积分（单条样本）
score = type_weight × quality_bonus

# type_weight：
#   SFT      → 1.5（标注成本高）
#   DPO      → 2.0（偏好数据稀缺且难产）
#   Pretrain → 0.5 × min(token_count/512, 3.0)

# quality_bonus：
#   铂金 → 2.0   黄金 → 1.5   白银 → 1.0   青铜 → 0.5

# 贡献比例
creator_ratio = creator_total_score / all_creators_total_score

# 创作者实得
creator_share = sale_amount × 0.70 × creator_ratio
```

---

## 六、数据集导出格式

### ShareGPT / ChatML 格式（sft_data.jsonl）
```json
{
  "conversations": [
    {"role": "system", "content": "你是..."},
    {"role": "user", "content": "请问..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

### Alpaca 格式（sft_alpaca.jsonl）
```json
{"instruction": "...", "input": "...", "output": "..."}
```

### DPO 格式（dpo_data.jsonl）
```json
{"prompt": "...", "chosen": "...", "rejected": "..."}
```

### 预训练格式（pretrain_data.jsonl）
```json
{"text": "...", "domain": "历史"}
```

---

## 七、快速使用示例

```python
import asyncio
from dataset.pipeline import DatasetProductionPipeline
from dataset.annotator import AnnotationMode
from dataset.schema import CreatorMaterial

async def main():
    # 1. 准备素材
    materials = [
        CreatorMaterial(
            creator_id="creator_001",
            raw_content="宋代文人士大夫阶层的精神世界...",
            material_type="text",
            metadata={"domain": "历史文化"}
        )
    ]

    # 2. 运行流水线
    pipeline = DatasetProductionPipeline(
        annotation_mode=AnnotationMode.AUTO_REVIEW,
        annotation_concurrency=5,
    )
    package = await pipeline.run(
        materials=materials,
        name="宋代历史文化SFT数据集_v1",
        description="覆盖宋代政治/经济/文化的高质量SFT数据",
        target_types=["sft", "dpo", "pretrain"],
        min_quality=6.0,
        price_cny=9800.0,
    )
    
    print(f"数据集ID: {package.package_id}")
    print(f"样本数: {package.total_samples}")
    print(f"ZIP: {package.export_paths['zip']}")

asyncio.run(main())
```

---

## 八、下一步建议（P0-P2）

### P0（立即）
- [ ] 接入现有 `rag_engine.py`：标注时利用已有知识库提升准确性
- [ ] 补充 `numpy` / `pandas` 到 requirements.txt
- [ ] `PLATFORM_FEE_RATIO` 移入 `.env` 可配置

### P1（近期）
- [ ] 将 `_material_store` / `_package_store` 迁移到 SQLite/PostgreSQL
- [ ] 人工复核 Web 界面：展示低置信度样本，支持编辑/通过/拒绝
- [ ] 企业客户门户：数据集预览、授权管理、下载记录

### P2（中期）
- [ ] 接入实际支付渠道（微信支付/支付宝）打通自动结算
- [ ] 批量标注任务队列（Celery + Redis）替代 BackgroundTasks
- [ ] 数据集版本管理（diff 对比、changelog）
- [ ] 向量数据库（Milvus/Qdrant）替代内存存储 SemanticDeduplicator
