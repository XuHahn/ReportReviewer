#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}══════════════════════════════════════════${NC}"
echo -e "${BLUE}  远程部署 — EMC检测报告智能审核系统${NC}"
echo -e "${BLUE}══════════════════════════════════════════${NC}"

# ── Gather info ─────────────────────────────────────────────────────────

read -rp "  远程服务器 (user@host): " REMOTE_HOST
if [ -z "$REMOTE_HOST" ]; then
  echo -e "${RED}[错误] 服务器地址不能为空${NC}"
  exit 1
fi

read -rp "  部署路径 (默认: ~/emc-reviewer): " REMOTE_PATH
REMOTE_PATH="${REMOTE_PATH:-~/emc-reviewer}"

read -rp "  服务端口 (默认: 8080): " REMOTE_PORT
REMOTE_PORT="${REMOTE_PORT:-8080}"

# Check API key locally
if [ ! -f "$SCRIPT_DIR/backend/.env" ]; then
  echo -e "\n${YELLOW}本地未找到 backend/.env${NC}"
  read -rp "  请输入 DEEPSEEK_API_KEY: " API_KEY
  if [ -z "$API_KEY" ]; then
    echo -e "${RED}[错误] API Key 不能为空${NC}"
    exit 1
  fi
  NEED_ENV=1
else
  NEED_ENV=0
fi

# ── Check SSH ───────────────────────────────────────────────────────────

echo -e "\n${BLUE}[1/3] 检查 SSH 连接...${NC}"
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$REMOTE_HOST" echo ok &>/dev/null 2>&1; then
  echo -e "  ${YELLOW}(需要输入服务器密码)${NC}"
fi
ssh -o ConnectTimeout=10 "$REMOTE_HOST" "echo '  ${GREEN}SSH 连接成功 ✓${NC}'"

# ── Sync files ──────────────────────────────────────────────────────────

echo -e "\n${BLUE}[2/3] 同步文件到远程服务器...${NC}"
REMOTE_DIR="${REMOTE_PATH%/}"
ssh "$REMOTE_HOST" "mkdir -p $REMOTE_DIR"

rsync -avz --delete \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='venv/' \
  --exclude='.venv/' \
  --exclude='node_modules/' \
  --exclude='dist/' \
  --exclude='*.db' \
  --exclude='logs/' \
  --exclude='data/' \
  --exclude='.DS_Store' \
  --exclude='.idea/' \
  --exclude='.vscode/' \
  --exclude='*.docx' \
  --exclude='*.pdf' \
  "$SCRIPT_DIR/" "$REMOTE_HOST:$REMOTE_DIR/"

echo -e "  ${GREEN}文件同步完成 ✓${NC}"

# ── Create remote .env if needed ────────────────────────────────────────

if [ "$NEED_ENV" = "1" ]; then
  echo -e "\n${YELLOW}创建远程 .env...${NC}"
  ssh "$REMOTE_HOST" "cat > $REMOTE_DIR/backend/.env << EOF
DEEPSEEK_API_KEY=$API_KEY
EOF"
fi

# ── Run deploy on remote ────────────────────────────────────────────────

echo -e "\n${BLUE}[3/3] 在远程服务器上构建并启动...${NC}"
ssh "$REMOTE_HOST" "cd $REMOTE_DIR && EMC_PORT=$REMOTE_PORT bash deploy.sh"

echo -e "\n${GREEN}全部完成！${NC}"
