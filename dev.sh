#!/bin/bash
# ─────────────────────────────────────────────────────────
# AI-Echo 开发环境一键启动脚本
# 用法：bash dev.sh
# ─────────────────────────────────────────────────────────

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 颜色输出 ──────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════╗"
echo "  ║   指数之源 AI-Echo  Dev Launcher ║"
echo "  ╚══════════════════════════════════╝"
echo -e "${NC}"

# ── 检查 .env ─────────────────────────────────────────────
if [ ! -f ".env" ]; then
  echo -e "${YELLOW}⚠ 未找到 .env 文件，从模板创建...${NC}"
  cp .env.example .env
  echo -e "${GREEN}✓ 已创建 .env，请检查配置后重新运行${NC}"
  exit 1
fi
echo -e "${GREEN}✓ .env 已加载${NC}"

# ── 检查 Node.js ──────────────────────────────────────────
if ! command -v node &> /dev/null; then
  echo -e "${RED}✗ 未找到 Node.js，请先安装: https://nodejs.org${NC}"
  exit 1
fi
echo -e "${GREEN}✓ Node.js $(node -v)${NC}"

# ── 检查 Python ───────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
  if command -v $cmd &> /dev/null && $cmd -c "import sys; exit(0 if sys.version_info >= (3,9) else 1)" 2>/dev/null; then
    PYTHON=$cmd
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo -e "${RED}✗ 未找到 Python 3.9+，请先安装${NC}"
  exit 1
fi
echo -e "${GREEN}✓ Python $($PYTHON --version 2>&1 | cut -d' ' -f2)${NC}"

# ── 安装前端依赖 ──────────────────────────────────────────
if [ ! -d "node_modules" ]; then
  echo -e "${YELLOW}>> 安装前端依赖 (npm install)...${NC}"
  npm install
fi
echo -e "${GREEN}✓ 前端依赖已就绪${NC}"

# ── 检查后端依赖 ──────────────────────────────────────────
if ! $PYTHON -c "import fastapi" 2>/dev/null; then
  echo -e "${YELLOW}>> 安装后端依赖 (pip install)...${NC}"
  $PYTHON -m pip install -r ai-echo-backend/requirements.txt
fi
echo -e "${GREEN}✓ 后端依赖已就绪${NC}"

echo ""
echo -e "${CYAN}>> 启动服务...${NC}"
echo -e "   前端: ${GREEN}http://localhost:5173${NC}"
echo -e "   后端: ${GREEN}http://localhost:8000${NC}"
echo -e "   API文档: ${GREEN}http://localhost:8000/docs${NC}"
echo ""
echo -e "${YELLOW}按 Ctrl+C 停止所有服务${NC}"
echo ""

# ── 启动后端（后台）──────────────────────────────────────
(
  cd ai-echo-backend
  $PYTHON -m uvicorn oracle_engine:app --host 0.0.0.0 --port 8000 --reload 2>&1 | \
    sed 's/^/[backend] /'
) &
BACKEND_PID=$!

# 等后端起来
sleep 2

# ── 启动前端 ─────────────────────────────────────────────
(npm run dev 2>&1 | sed 's/^/[frontend] /') &
FRONTEND_PID=$!

# ── 捕获退出信号，同时停止两个进程 ──────────────────────
cleanup() {
  echo -e "\n${YELLOW}>> 停止服务...${NC}"
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
  wait $BACKEND_PID $FRONTEND_PID 2>/dev/null
  echo -e "${GREEN}✓ 已停止${NC}"
}
trap cleanup EXIT INT TERM

# 等待任意一个进程退出
wait
