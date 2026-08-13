#!/usr/bin/env bash
set -euo pipefail

RUNTIME_HOME="${UNIFIED_REVIEW_MODEL_RUNTIME_HOME:-$HOME/.local/share/report-reviewer/models}"
PYTHON_BIN="${UNIFIED_REVIEW_MODEL_PYTHON:-python3}"
MLX_VENV="$RUNTIME_HOME/mlx-runtime"

mkdir -p "$RUNTIME_HOME" "$RUNTIME_HOME/logs"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This bootstrap is for Apple Silicon development machines." >&2
  exit 1
fi

if [[ ! -x "$MLX_VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$MLX_VENV"
fi
"$MLX_VENV/bin/python" -m pip install --upgrade pip wheel setuptools
"$MLX_VENV/bin/python" -m pip install "mlx-vlm==0.6.6" "mlx==0.32.0"

cat <<EOF
Unified review development runtime is ready:
  MLX: $MLX_VENV

Model weights are downloaded into the Hugging Face cache on first use.
Start the visual model with:
  scripts/model_runtime/start_qwen_vision.sh
EOF
