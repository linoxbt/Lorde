#!/usr/bin/env bash
# Step 0: serve models with vLLM's OpenAI-compatible server (forge-serve env).
#
#   bash forge/00_serve.sh teacher   # Aya Expanse 8B across both T4s  -> :8000 (model "teacher")
#   bash forge/00_serve.sh student   # Tiny Aya Global, one GPU         -> :8001 (model "student")
#   bash forge/00_serve.sh lorde     # runs/lorde-v1/merged, one GPU    -> :8002 (model "lorde")
#   bash forge/00_serve.sh stop      # stop every server started here
#
# Pin the GPU for single-GPU servers with CUDA_VISIBLE_DEVICES=0|1.
# Env: HF_TOKEN (gated Cohere models), VLLM_API_KEY (optional), VLLM_EXTRA_ARGS,
#      TEACHER_MODEL, STUDENT_MODEL, LORDE_MODEL, MAX_MODEL_LEN, DTYPE.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs

ROLE="${1:-}"
TEACHER_MODEL="${TEACHER_MODEL:-CohereLabs/aya-expanse-8b}"
STUDENT_MODEL="${STUDENT_MODEL:-CohereLabs/tiny-aya-global}"
LORDE_MODEL="${LORDE_MODEL:-runs/lorde-v1/merged}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"

# T4 (compute capability 7.5) has no bf16: use fp16 there, bf16 on L4/A100.
if [[ -z "${DTYPE:-}" ]]; then
  CC=$(python -c "import torch;print(torch.cuda.get_device_capability()[0])" 2>/dev/null || echo 8)
  if [[ "$CC" -lt 8 ]]; then DTYPE=half; else DTYPE=bfloat16; fi
fi

KEY_ARGS=(); AUTH=()
if [[ -n "${VLLM_API_KEY:-}" ]]; then
  KEY_ARGS=(--api-key "$VLLM_API_KEY"); AUTH=(-H "Authorization: Bearer $VLLM_API_KEY")
fi

start() {  # name port model extra...
  local name=$1 port=$2 model=$3; shift 3
  if curl -sf "http://localhost:${port}/v1/models" "${AUTH[@]}" >/dev/null 2>&1; then
    echo "[serve] ${name} already up on :${port}"; return
  fi
  echo "[serve] starting ${name} (${model}) on :${port} dtype=${DTYPE}"
  nohup vllm serve "$model" \
    --served-model-name "$name" --port "$port" --dtype "$DTYPE" \
    --max-model-len "$MAX_MODEL_LEN" --gpu-memory-utilization "${GPU_UTIL:-0.90}" \
    --max-num-seqs "${MAX_NUM_SEQS:-64}" --generation-config vllm \
    "${KEY_ARGS[@]}" "$@" ${VLLM_EXTRA_ARGS:-} > "logs/vllm_${name}.log" 2>&1 &
  echo $! > "logs/vllm_${name}.pid"
  for i in $(seq 1 180); do
    if curl -sf "http://localhost:${port}/v1/models" "${AUTH[@]}" >/dev/null 2>&1; then
      echo "[serve] ${name} ready after $((i * 5))s"; return
    fi
    if ! kill -0 "$(cat "logs/vllm_${name}.pid")" 2>/dev/null; then
      echo "[serve] ${name} died; last log lines:"; tail -40 "logs/vllm_${name}.log"; exit 1
    fi
    sleep 5
  done
  echo "[serve] ${name} not ready after 15 min; see logs/vllm_${name}.log"; exit 1
}

case "$ROLE" in
  teacher) start teacher 8000 "$TEACHER_MODEL" --tensor-parallel-size "${TP:-2}" ;;
  student) start student 8001 "$STUDENT_MODEL" ;;
  lorde)   start lorde 8002 "$LORDE_MODEL" ;;
  stop)
    for f in logs/vllm_*.pid; do
      [[ -e "$f" ]] || continue
      pid=$(cat "$f"); kill "$pid" 2>/dev/null && echo "[serve] stopped $(basename "$f" .pid)"
      for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
      kill -9 "$pid" 2>/dev/null || true; rm -f "$f"
    done
    pkill -f "vllm serve" 2>/dev/null || true ;;
  *) echo "usage: $0 teacher|student|lorde|stop"; exit 2 ;;
esac
