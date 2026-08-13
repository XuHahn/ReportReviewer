#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONDA_ENV_NAME="${REPORT_REVIEWER_CONDA_ENV:-report-reviewer}"
cd "$PROJECT_DIR"
echo "============================================"
echo " EMC报告审核系统 — 一键环境安装"
echo "============================================"

echo ""
echo "[1/2] 创建 Conda 后端环境并安装依赖..."
command -v conda >/dev/null || { echo "缺少 Conda，请先安装 Miniconda" >&2; exit 1; }
if ! conda run -n "$CONDA_ENV_NAME" python -c "import sys" >/dev/null 2>&1; then
  conda create -y -n "$CONDA_ENV_NAME" -c conda-forge --override-channels python=3.13 pip
fi
conda run -n "$CONDA_ENV_NAME" python -m pip install \
  -r "$PROJECT_DIR/backend/requirements.txt" \
  -r "$PROJECT_DIR/backend/tests/requirements-test.txt"

echo ""
echo "[2/2] 安装统一前端依赖..."
(cd "$PROJECT_DIR/frontend-v2" && npm install)

echo ""
echo "============================================"
echo " 安装完成！"
echo "============================================"
echo ""

if [ ! -f backend/.env ]; then
  echo "警告: 未检测到 backend/.env"
  echo "请创建 backend/.env 并配置 DEEPSEEK_API_KEY："
  echo '  echo "DEEPSEEK_API_KEY=你的密钥" > backend/.env'
else
  echo "已检测到 backend/.env"
fi

echo ""
echo "启动命令："
echo "  后端:  cd backend && conda run -n $CONDA_ENV_NAME python -m uvicorn main:app --port 8000"
echo "  前端:  cd frontend-v2 && npm run dev"
echo "  一键:  bash start.sh"
