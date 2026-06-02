#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT=8000
FRONTEND_PORT=5173
ADMIN_PORT=5174
LOG_DIR="$PROJECT_DIR/logs"
BACKEND_PID=""
FRONTEND_PID=""
ADMIN_PID=""

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

mkdir -p "$LOG_DIR"

cleanup() {
    echo -e "\n${BLUE}正在关闭服务...${NC}"
    [ -n "$BACKEND_PID" ] && kill $BACKEND_PID 2>/dev/null
    [ -n "$FRONTEND_PID" ] && kill $FRONTEND_PID 2>/dev/null
    [ -n "$ADMIN_PID" ] && kill $ADMIN_PID 2>/dev/null
    wait $BACKEND_PID 2>/dev/null || true
    wait $FRONTEND_PID 2>/dev/null || true
    wait $ADMIN_PID 2>/dev/null || true
    echo -e "${GREEN}已关闭所有服务${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# Kill any processes already on our ports
kill_port() {
    local pid=$(lsof -ti ":$1" 2>/dev/null)
    if [ -n "$pid" ]; then
        echo -e "  ${YELLOW}端口 $1 被占用 (PID:$pid)，正在释放...${NC}"
        kill $pid 2>/dev/null && sleep 0.5
    fi
}

# ── Pre-flight checks ────────────────────────────────────────────────
echo -e "${BLUE}══════════════════════════════════════════${NC}"
echo -e "${BLUE}  EMC检测报告智能审核系统${NC}"
echo -e "${BLUE}══════════════════════════════════════════${NC}"

# Check Python
PYTHON=$(command -v python3 || echo "")
if [ -z "$PYTHON" ]; then
    echo -e "${RED}[错误] 未找到 python3，请先安装 Python 3.10+${NC}"
    exit 1
fi
echo -e "  Python: $($PYTHON --version)"

# Check Node
NODE=$(command -v node || echo "")
if [ -z "$NODE" ]; then
    echo -e "${RED}[错误] 未找到 node，请先安装 Node.js 18+${NC}"
    exit 1
fi
echo -e "  Node:   $(node --version)"

# Check .env
if [ ! -f "$PROJECT_DIR/backend/.env" ]; then
    echo -e "${RED}[错误] 未找到 backend/.env，请创建并设置 DEEPSEEK_API_KEY${NC}"
    exit 1
fi
echo -e "  .env:   已配置"

# ── Release ports ─────────────────────────────────────────────────────
kill_port $FRONTEND_PORT
kill_port $ADMIN_PORT
kill_port $BACKEND_PORT

# ── Detect local network IP ───────────────────────────────────────────
get_local_ip() {
    ipconfig getifaddr en0 2>/dev/null || \
    ifconfig en0 2>/dev/null | grep 'inet ' | awk '{print $2}' | head -1 || \
    hostname -I 2>/dev/null | awk '{print $1}' || \
    echo "unknown"
}
LOCAL_IP=$(get_local_ip)

# ── Backend ───────────────────────────────────────────────────────────
echo -e "\n${GREEN}[1/3] 启动后端...${NC}"
cd "$PROJECT_DIR/backend"

if [ ! -d "venv" ]; then
    echo "  创建虚拟环境..."
    $PYTHON -m venv venv
fi
source venv/bin/activate

if [ ! -f ".deps_installed" ] || [ requirements.txt -nt .deps_installed ]; then
    echo "  安装依赖..."
    pip install -q -r requirements.txt && touch .deps_installed
fi

uvicorn main:app --host 0.0.0.0 --port $BACKEND_PORT \
    > "$LOG_DIR/access.log" 2>"$LOG_DIR/backend.log" &
BACKEND_PID=$!
echo "  后端 PID:$BACKEND_PID → http://0.0.0.0:$BACKEND_PORT"

# ── Frontend ──────────────────────────────────────────────────────────
echo -e "\n${GREEN}[2/3] 启动前端...${NC}"
cd "$PROJECT_DIR/frontend"

if [ ! -d "node_modules" ]; then
    echo "  安装依赖..."
    npm install --silent
fi

npx vite --host 0.0.0.0 > "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo "  前端 PID:$FRONTEND_PID → http://0.0.0.0:$FRONTEND_PORT"

# ── Admin Frontend ───────────────────────────────────────────────────────
echo -e "\n${GREEN}[3/3] 启动后台管理...${NC}"
cd "$PROJECT_DIR/admin"

if [ ! -d "node_modules" ]; then
    echo "  安装依赖..."
    npm install --silent
fi

npx vite --host 0.0.0.0 > "$LOG_DIR/admin.log" 2>&1 &
ADMIN_PID=$!
echo "  后台管理 PID:$ADMIN_PID → http://0.0.0.0:$ADMIN_PORT"

# ── Health check ──────────────────────────────────────────────────────
echo -e "\n${BLUE}等待服务就绪...${NC}"
for i in $(seq 1 20); do
    sleep 0.5
    if curl -sf "http://localhost:$BACKEND_PORT/api/health" > /dev/null 2>&1; then
        echo -e "  ${GREEN}后端就绪 ✓${NC}"
        break
    fi
    if [ $i -eq 20 ]; then
        echo -e "  ${RED}后端启动超时，请检查日志: $LOG_DIR/backend.log${NC}"
        exit 1
    fi
done

for i in $(seq 1 10); do
    sleep 0.5
    if curl -sf "http://localhost:$FRONTEND_PORT" > /dev/null 2>&1; then
        echo -e "  ${GREEN}前端就绪 ✓${NC}"
        break
    fi
    if [ $i -eq 10 ]; then
        echo -e "  ${YELLOW}前端启动中 (未能在5s内响应)，请稍后刷新浏览器${NC}"
    fi
done

for i in $(seq 1 10); do
    sleep 0.5
    if curl -sf "http://localhost:$ADMIN_PORT" > /dev/null 2>&1; then
        echo -e "  ${GREEN}后台管理就绪 ✓${NC}"
        break
    fi
    if [ $i -eq 10 ]; then
        echo -e "  ${YELLOW}后台管理启动中 (未能在5s内响应)，请稍后刷新浏览器${NC}"
    fi
done

# ── Check processes survived ──────────────────────────────────────────
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo -e "${RED}[错误] 后端进程已退出，查看日志:${NC}"
    echo -e "${RED}  $(tail -5 "$LOG_DIR/backend.log")${NC}"
    exit 1
fi
if ! kill -0 $FRONTEND_PID 2>/dev/null; then
    echo -e "${RED}[错误] 前端进程已退出，查看日志: $LOG_DIR/frontend.log${NC}"
    exit 1
fi
if ! kill -0 $ADMIN_PID 2>/dev/null; then
    echo -e "${RED}[错误] 后台管理进程已退出，查看日志: $LOG_DIR/admin.log${NC}"
    exit 1
fi

# ── Summary ───────────────────────────────────────────────────────────
echo -e "\n${GREEN}══════════════════════════════════════════${NC}"
echo -e "${GREEN}  本机访问:${NC}"
echo -e "${GREEN}    前端 (审核): http://localhost:$FRONTEND_PORT${NC}"
echo -e "${GREEN}    后台管理:    http://localhost:$ADMIN_PORT${NC}"
echo -e "${GREEN}    后端:        http://localhost:$BACKEND_PORT${NC}"
echo -e "${GREEN}    API文档:     http://localhost:$BACKEND_PORT/docs${NC}"
if [ "$LOCAL_IP" != "unknown" ] && [ -n "$LOCAL_IP" ]; then
    echo -e "\n${YELLOW}  局域网访问:${NC}"
    echo -e "${YELLOW}    前端 (审核): http://$LOCAL_IP:$FRONTEND_PORT${NC}"
    echo -e "${YELLOW}    后台管理:    http://$LOCAL_IP:$ADMIN_PORT${NC}"
    echo -e "${YELLOW}    后端:        http://$LOCAL_IP:$BACKEND_PORT${NC}"
fi
echo -e "\n${BLUE}  日志文件:${NC}"
echo -e "${BLUE}    审核日志:   $LOG_DIR/app.log${NC}"
echo -e "${BLUE}    HTTP访问:   $LOG_DIR/access.log${NC}"
echo -e "${BLUE}    后端输出:   $LOG_DIR/backend.log${NC}"
echo -e "${BLUE}    前端输出:   $LOG_DIR/frontend.log${NC}"
echo -e "${BLUE}    后台管理输出: $LOG_DIR/admin.log${NC}"
echo -e "${YELLOW}  审计日志: http://localhost:$BACKEND_PORT/api/logs${NC}"
echo -e "${GREEN}  Ctrl+C 关闭所有服务${NC}"
echo -e "${GREEN}══════════════════════════════════════════${NC}"

wait
