#!/usr/bin/env bash
set -euo pipefail

python -m post_training_rsi \
  --config "${RSI_CONFIG:-configs/pipeline.example.json}" \
  --workspace "${RSI_WORKSPACE:-artifacts/demo}" \
  demo "$@"
