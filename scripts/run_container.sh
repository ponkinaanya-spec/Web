#!/usr/bin/env sh
set -eu

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed or not on PATH" >&2
  exit 1
fi

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
MODEL_DIR="$PROJECT_DIR/models"

for model in \
  anatomy_spine_hip_unknown_efficientnet_b0.pt \
  spine_artifact_densenet121.pt \
  spine_positioning_efficientnet_b0.pt \
  hip_positioning_densenet121_candidate.pt \
  hip_binary_candidate.pt
do
  if [ ! -f "$MODEL_DIR/$model" ]; then
    echo "Missing model checkpoint: $MODEL_DIR/$model" >&2
    exit 1
  fi
done

docker compose -f "$PROJECT_DIR/docker-compose.yml" up --build

