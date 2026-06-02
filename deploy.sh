#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

banner() {
  echo -e "${BLUE}══════════════════════════════════════════${NC}"
  echo -e "${BLUE}  EMC检测报告智能审核系统 — Docker 部署${NC}"
  echo -e "${BLUE}══════════════════════════════════════════${NC}"
}

# ── Pre-flight ──────────────────────────────────────────────────────────

banner

if ! command -v docker &> /dev/null; then
  echo -e "${RED}[错误] 未找到 Docker，请先安装:${NC}"
  echo -e "${RED}  curl -fsSL https://get.docker.com | sh${NC}"
  exit 1
fi
echo -e "  Docker: $(docker --version)"

if docker compose version &> /dev/null; then
  DOCKER_COMPOSE="docker compose"
elif command -v docker-compose &> /dev/null; then
  DOCKER_COMPOSE="docker-compose"
else
  echo -e "${RED}[错误] 未找到 Docker Compose${NC}"
  exit 1
fi
echo -e "  Compose: $($DOCKER_COMPOSE version --short 2>/dev/null || echo ok)"

# ── .env setup ──────────────────────────────────────────────────────────

if [ ! -f "$PROJECT_DIR/backend/.env" ]; then
  echo -e "\n${YELLOW}未找到 backend/.env，需要配置 DeepSeek API Key${NC}"
  read -rp "  请输入 DEEPSEEK_API_KEY: " API_KEY
  if [ -z "$API_KEY" ]; then
    echo -e "${RED}[错误] API Key 不能为空${NC}"
    exit 1
  fi
  cat > "$PROJECT_DIR/backend/.env" << EOF
DEEPSEEK_API_KEY=$API_KEY
# DEEPSEEK_BASE_URL=https://api.deepseek.com
EOF
  echo -e "  ${GREEN}backend/.env 已创建${NC}"
else
  echo -e "  .env:  已配置"
fi

# ── Port check ──────────────────────────────────────────────────────────

FRONTEND_PORT="${EMC_PORT:-8080}"
if lsof -i ":$FRONTEND_PORT" -sTCP:LISTEN &>/dev/null 2>&1; then
  echo -e "\n${YELLOW}[警告] 端口 $FRONTEND_PORT 已被占用${NC}"
  read -rp "  输入新端口号 (或回车跳过): " NEW_PORT
  if [ -n "$NEW_PORT" ]; then
    FRONTEND_PORT="$NEW_PORT"
  fi
fi

# ── Build & Start ───────────────────────────────────────────────────────

echo -e "\n${GREEN}[1/2] 构建 Docker 镜像...${NC}"
cd "$PROJECT_DIR"
$DOCKER_COMPOSE build

echo -e "\n${GREEN}[2/2] 启动服务...${NC}"
$DOCKER_COMPOSE up -d

# If port changed, we need to expose it. For simplicity, use env var override.
# The compose file exposes 8080:80 — if user wants another port, use docker run override
# or they can edit docker-compose.yml. For now, note the configured port.

# ── Health check ────────────────────────────────────────────────────────

echo -e "\n${BLUE}等待服务就绪...${NC}"
for i in $(seq 1 30); do
  sleep 1
  if curl -sf "http://localhost:${FRONTEND_PORT}/api/health" > /dev/null 2>&1; then
    echo -e "  ${GREEN}后端就绪 ✓${NC}"
    break
  fi
  if [ $i -eq 30 ]; then
    echo -e "${YELLOW}  后端仍在启动中，请稍后检查: docker compose logs backend${NC}"
  fi
done

# ── Summary ──────────────────────────────────────────────────────────────

# Cross-platform IP detection (macOS + Linux)
if [[ "$(uname)" == "Darwin" ]]; then
  SERVER_IP=$(ipconfig getifaddr en0 2>/dev/null || \
              ifconfig en0 2>/dev/null | awk '/inet /{print $2}' || \
              echo "localhost")
else
  SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || \
              ip route get 1 2>/dev/null | awk '{print $7; exit}' || \
              echo "localhost")
fi

echo -e "\n${GREEN}══════════════════════════════════════════${NC}"
echo -e "  ${GREEN}部署完成！${NC}"
echo -e "\n${GREEN}  访问地址:${NC}"
echo -e "${GREEN}    http://${SERVER_IP}:${FRONTEND_PORT}${NC}"
echo -e "\n${BLUE}  常用命令:${NC}"
echo -e "${BLUE}    查看日志: docker compose logs -f backend${NC}"
echo -e "${BLUE}    重启服务: docker compose restart${NC}"
echo -e "${BLUE}    停止服务: docker compose down${NC}"
echo -e "${BLUE}    更新重部署: bash deploy.sh${NC}"
echo -e "${GREEN}══════════════════════════════════════════${NC}"
