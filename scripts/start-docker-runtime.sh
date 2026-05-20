#!/usr/bin/env bash
set -euo pipefail

TIMEOUT_SECONDS="${AI_OBSIDIAN_DOCKER_TIMEOUT:-90}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI is missing. Install Docker Desktop for Mac first." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Starting Docker Desktop..."
  docker desktop start >/dev/null 2>&1 || true
fi

echo "Waiting for Docker daemon..."
deadline=$((SECONDS + TIMEOUT_SECONDS))
until docker info >/dev/null 2>&1; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "Docker daemon did not become ready in ${TIMEOUT_SECONDS}s." >&2
    exit 1
  fi
  sleep 2
done

if ! docker model status >/dev/null 2>&1; then
  echo "Docker is running, but Docker Model Runner is not ready." >&2
  echo "Enable Docker Model Runner in Docker Desktop, then retry." >&2
  exit 1
fi

if ! curl -fsS --max-time 5 http://localhost:12434/engines/v1/models >/dev/null 2>&1; then
  echo "Enabling Docker Model Runner TCP endpoint on port 12434..."
  docker desktop enable model-runner --tcp=12434
fi

exec ai-obsidian docker start "$@"
