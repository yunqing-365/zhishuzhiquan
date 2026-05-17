# 指数之源 · AI-Echo Protocol

> 多模态 AI 数据资产定价预言机 + 链上产权保护协议

## 系统架构

```
前端 (React 19 + Vite)
  ↓ /api/* 代理
后端 (FastAPI + ChromaDB)      ←→  oracle_engine v4
  ↓ 估值结果
智能合约 (Solidity 0.8.20)     ←→  AI_Echo_Contracts v5.0
```

**支持模态：** 文本 · 图像 · 音频（视频适配器开发中）

**三步流程：** 资产录入 → 预言机估值 → 合约分账

---

## 快速启动

### 方式一：一键脚本（推荐）

```bash
# Mac / Linux
bash dev.sh

# Windows
dev.bat
```

### 方式二：手动启动

**第一步：配置环境变量**

```bash
cp .env.example .env
```

**第二步：启动后端**

```bash
cd ai-echo-backend
pip install -r requirements.txt
python -m uvicorn oracle_engine:app --host 0.0.0.0 --port 8000 --reload
```

**第三步：启动前端（新开终端）**

```bash
npm install
npm run dev
```

浏览器打开 http://localhost:5173

---

## 目录结构

```
zhishuzhiquan/
├── src/
│   ├── App.jsx                    # 主路由（3步流程）
│   ├── DataInputScreen.jsx        # 步骤1：资产录入
│   ├── OracleValuationScreen.jsx  # 步骤2：预言机估值
│   └── SmartSplitScreen.jsx       # 步骤3：合约分账
├── ai-echo-backend/
│   ├── oracle_engine.py           # 主服务
│   ├── scene_classifier.py        # 场景识别
│   ├── scoring.py                 # TEV 定价模型
│   ├── adapters/                  # 模态适配器
│   └── requirements.txt           # Python 依赖
├── AI_Echo_Contracts.sol          # Solidity 合约 v5.0
├── .env.example                   # 环境变量模板
├── vite.config.js                 # Vite 配置（含代理）
├── dev.sh                         # 一键启动 (Mac/Linux)
└── dev.bat                        # 一键启动 (Windows)
```

---

## API 文档

后端启动后访问：http://localhost:8000/docs

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/valuate` | POST | 多模态资产估值 |
| `/api/scenes` | GET | 支持的场景列表 |
| `/api/health` | GET | 健康检查 |

---

## 环境变量说明

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VITE_API_URL` | `http://localhost:8000` | 后端地址 |
| `BACKEND_HOST` | `0.0.0.0` | 后端监听地址 |
| `BACKEND_PORT` | `8000` | 后端端口 |
| `ALLOWED_ORIGINS` | `*`（开发） | 允许跨域的前端地址 |
| `HF_ENDPOINT` | `https://hf-mirror.com` | HuggingFace 镜像 |

---

## 升级路线

- [x] **阶段1** 环境配置、API 对接、启动脚本
- [ ] **阶段2** 真实 AI 模型推理、数据持久化、ZK 证明
- [ ] **阶段3** Web3 钱包连接、合约测试网部署
- [ ] **阶段4** CI/CD、监控告警、主网上线
