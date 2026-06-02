#!/bin/bash
set -e

cd "$(dirname "$0")"
echo "============================================"
echo " EMC报告审核系统 — 一键环境安装"
echo "============================================"

echo ""
echo "[1/3] 安装 Python 后端依赖..."
cd backend && pip install -r requirements.txt && cd ..

echo ""
echo "[2/3] 安装前端依赖..."
cd frontend && npm install && cd ..

echo ""
echo "[3/3] 安装后台管理依赖..."
cd admin && npm install && cd ..

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
echo "  后端:  cd backend && uvicorn main:app --port 8000"
echo "  前端:  cd frontend && npm run dev"
echo "  后台:  cd admin && npm run dev"
echo "  一键:  bash start.sh"
