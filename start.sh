#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONDA_ENV_NAME="${REPORT_REVIEWER_CONDA_ENV:-report-reviewer}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
FRONTEND_V2_PORT="${FRONTEND_V2_PORT:-5174}"
VISION_READY_TIMEOUT_SEC="${VISION_READY_TIMEOUT_SEC:-600}"
LOG_DIR="$PROJECT_DIR/logs"
BACKEND_PID=""
FRONTEND_PID=""
FRONTEND_V2_PID=""
VISION_PID=""
VISION_MONITOR_PID=""

mkdir -p "$LOG_DIR"

cleanup() {
  trap - SIGINT SIGTERM EXIT
  echo "正在关闭服务..."
  [[ -n "$BACKEND_PID" ]] && kill "$BACKEND_PID" 2>/dev/null || true
  [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
  [[ -n "$FRONTEND_V2_PID" ]] && kill "$FRONTEND_V2_PID" 2>/dev/null || true
  [[ -n "$VISION_MONITOR_PID" ]] && kill "$VISION_MONITOR_PID" 2>/dev/null || true
  [[ -n "$VISION_PID" ]] && kill "$VISION_PID" 2>/dev/null || true
  wait "$BACKEND_PID" 2>/dev/null || true
  wait "$FRONTEND_PID" 2>/dev/null || true
  wait "$FRONTEND_V2_PID" 2>/dev/null || true
  wait "$VISION_MONITOR_PID" 2>/dev/null || true
  wait "$VISION_PID" 2>/dev/null || true
}
trap cleanup SIGINT SIGTERM EXIT

for command in conda node npm curl; do
  command -v "$command" >/dev/null || { echo "缺少运行依赖: $command" >&2; exit 1; }
done
BACKEND_PYTHON="$(conda run -n "$CONDA_ENV_NAME" python -c 'import sys; print(sys.executable)' 2>/dev/null)" || {
  echo "未找到 Conda 环境 $CONDA_ENV_NAME，请先运行 ./setup.sh" >&2
  exit 1
}
[[ -f "$PROJECT_DIR/backend/.env" ]] || {
  echo "未找到 backend/.env，请复制 backend/.env.example 后填写配置" >&2
  exit 1
}

# Export the same configuration consumed by python-dotenv so optional local
# model processes and the backend cannot drift onto different ports/models.
set -a
# shellcheck disable=SC1091
source "$PROJECT_DIR/backend/.env"
set +a

release_port() {
  local occupied
  occupied="$(lsof -ti ":$1" 2>/dev/null || true)"
  [[ -z "$occupied" ]] || kill $occupied 2>/dev/null || true
}
release_port "$BACKEND_PORT"
release_port "$FRONTEND_PORT"
release_port "$FRONTEND_V2_PORT"

cd "$PROJECT_DIR/backend"
"$BACKEND_PYTHON" -m uvicorn main:app --host 0.0.0.0 --port "$BACKEND_PORT" >"$LOG_DIR/access.log" 2>"$LOG_DIR/backend.log" &
BACKEND_PID=$!

# The API is the first user-facing process.  It must be healthy even while
# the optional local vision model is still loading in the background.
for _ in $(seq 1 60); do
  curl -sf "http://localhost:$BACKEND_PORT/api/health" >/dev/null && break
  sleep 0.5
done
curl -sf "http://localhost:$BACKEND_PORT/api/health" >/dev/null || {
  echo "后端启动失败，请检查 $LOG_DIR/backend.log" >&2
  exit 1
}
echo "后端已启动，视觉模型将异步加载。"

cd "$PROJECT_DIR/frontend"
[[ -d node_modules ]] || npm install --silent
npx vite --host 0.0.0.0 --port "$FRONTEND_PORT" >"$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

cd "$PROJECT_DIR/frontend-v2"
[[ -d node_modules ]] || npm install --silent
npx vite --host 0.0.0.0 --port "$FRONTEND_V2_PORT" >"$LOG_DIR/frontend-v2.log" 2>&1 &
FRONTEND_V2_PID=$!

START_VISION="$(awk -F= '$1 == "UNIFIED_REVIEW_START_LOCAL_VISION" {print tolower($2)}' "$PROJECT_DIR/backend/.env" | tail -1 | tr -d '[:space:]')"
if [[ "$START_VISION" == "true" ]]; then
  # A previous launcher may have exited without owning its model child. If
  # the old listener is not released, the new model can load successfully,
  # fail to bind, and the UI may read a stale readiness response.
  release_port "${UNIFIED_REVIEW_VISION_PORT:-8081}"
  "$PROJECT_DIR/scripts/model_runtime/start_qwen_vision.sh" >"$LOG_DIR/qwen-vision.log" 2>&1 &
  VISION_PID=$!
  VISION_MODELS_URL="${UNIFIED_REVIEW_VISION_BASE_URL%/}/models"
  echo "视觉模型已在后台启动（PID:$VISION_PID），页面会显示‘视觉模型准备中’并自动更新。"

  # Readiness is observability only; it must never block or tear down the API.
  # The health endpoint and frontend poll the same model endpoint for live UI
  # state, while this watcher leaves a concise launcher milestone in the log.
  (
    for _ in $(seq 1 "$VISION_READY_TIMEOUT_SEC"); do
      if curl -sf --max-time 2 "$VISION_MODELS_URL" >/dev/null; then
        echo "$(date '+%Y-%m-%dT%H:%M:%S%z') launcher readiness: ready" >>"$LOG_DIR/qwen-vision.log"
        exit 0
      fi
      if ! kill -0 "$VISION_PID" 2>/dev/null; then
        echo "$(date '+%Y-%m-%dT%H:%M:%S%z') launcher readiness: process exited; API remains available" >>"$LOG_DIR/qwen-vision.log"
        exit 0
      fi
      sleep 1
    done
    echo "$(date '+%Y-%m-%dT%H:%M:%S%z') launcher readiness: timeout after ${VISION_READY_TIMEOUT_SEC}s; API remains available" >>"$LOG_DIR/qwen-vision.log"
  ) &
  VISION_MONITOR_PID=$!
else
  echo "未启用本地视觉模型，页面会根据后端配置显示视觉服务状态。"
fi

echo "统一审核系统已启动"
echo "前端(v1): http://localhost:$FRONTEND_PORT"
echo "前端(v2): http://localhost:$FRONTEND_V2_PORT"
echo "后端:    http://localhost:$BACKEND_PORT"
echo "API:  http://localhost:$BACKEND_PORT/docs"
echo "日志: $LOG_DIR"
echo "Ctrl+C 关闭"
wait
