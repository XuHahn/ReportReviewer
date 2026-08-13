#!/usr/bin/env bash
set -euo pipefail

RUNTIME_HOME="${UNIFIED_REVIEW_MODEL_RUNTIME_HOME:-$HOME/.local/share/report-reviewer/models}"
MLX_VENV="$RUNTIME_HOME/mlx-runtime"
HOST="${UNIFIED_REVIEW_VISION_HOST:-127.0.0.1}"
PORT="${UNIFIED_REVIEW_VISION_PORT:-8081}"
MODEL="${UNIFIED_REVIEW_VISION_MODEL:-mlx-community/Qwen3-VL-4B-Instruct-3bit}"
MEMORY_BYTES="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
if [[ "$MEMORY_BYTES" -ge 17179869184 ]]; then
  DEFAULT_KV_SIZE=12288
else
  DEFAULT_KV_SIZE=8192
fi

if [[ ! -x "$MLX_VENV/bin/mlx_vlm.server" ]]; then
  echo "MLX runtime is missing. Run scripts/model_runtime/bootstrap_dev.sh first." >&2
  exit 1
fi

# Real review requests combine one rendered page and structured JSON.  The
# cache default follows physical memory: 12K on >=16 GiB Apple Silicon, 8K on
# smaller machines.  Model choice remains explicit so the backend and server
# can never silently load different weights.
exec "$MLX_VENV/bin/mlx_vlm.server" \
  --host "$HOST" --port "$PORT" --model "$MODEL" \
  --max-kv-size "${UNIFIED_REVIEW_VISION_MAX_KV_SIZE:-$DEFAULT_KV_SIZE}" \
  --vision-cache-size "${UNIFIED_REVIEW_VISION_CACHE_SIZE:-2}" \
  --log-level INFO
