#!/usr/bin/env bash
set -Eeuo pipefail
RELEASE=${RELEASE:-market-maker-scout}
NAMESPACE=${NAMESPACE:-market-maker-scout}
IMAGE_REPOSITORY=${IMAGE_REPOSITORY:?IMAGE_REPOSITORY is required}
IMAGE_TAG=${IMAGE_TAG:?IMAGE_TAG is required}
IMAGE_PULL_SECRET=${IMAGE_PULL_SECRET:-}
SLACK_WEBHOOK_URL=${SLACK_WEBHOOK_URL:-}
CODEX_ADVISOR_TIMEOUT_SECONDS=${CODEX_ADVISOR_TIMEOUT_SECONDS:-90}
ADVICE_FILE="${GITHUB_WORKSPACE:-$PWD}/deployment-advice.txt"

json_quote() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
  else
    python -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
  fi
}

slack() {
  [ -z "$SLACK_WEBHOOK_URL" ] && return 0
  payload=$(json_quote "$1" 2>/dev/null) || return 0
  curl -fsS -X POST -H 'Content-type: application/json' \
    --data "{\"text\":$payload}" \
    "$SLACK_WEBHOOK_URL" >/dev/null || true
}

run_with_timeout() {
  local seconds="$1"
  local output_file="$2"
  shift 2

  "$@" > "$output_file" 2>&1 &
  local pid=$!
  local elapsed=0

  while kill -0 "$pid" 2>/dev/null; do
    if [ "$elapsed" -ge "$seconds" ]; then
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      return 124
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  wait "$pid"
}

slack "🚀 Deploying $RELEASE:$IMAGE_TAG to namespace $NAMESPACE"
helm_args=(
  upgrade --install "$RELEASE" helm/market-maker-scout
  --namespace "$NAMESPACE"
  --create-namespace
  --set "image.repository=$IMAGE_REPOSITORY"
  --set "image.tag=$IMAGE_TAG"
  --wait
  --timeout 5m
)
if [ -n "$IMAGE_PULL_SECRET" ]; then
  helm_args+=(--set "imagePullSecrets[0].name=$IMAGE_PULL_SECRET")
fi

set +e
output=$(helm "${helm_args[@]}" 2>&1)
rc=$?
set -e
if [ $rc -eq 0 ]; then
  slack "✅ Deployment succeeded: $RELEASE:$IMAGE_TAG"
  exit 0
fi

kubectl get all -n "$NAMESPACE" > /tmp/k8s-state.txt 2>&1 || true
kubectl get events -n "$NAMESPACE" --sort-by=.lastTimestamp >> /tmp/k8s-state.txt 2>&1 || true
kubectl describe pods -n "$NAMESPACE" >> /tmp/k8s-state.txt 2>&1 || true
printf '%s\n' "$output" > /tmp/helm-error.txt

advice="Codex advisor unavailable"
if command -v codex >/dev/null 2>&1; then
  advisor_output="/tmp/codex-advisor.txt"
  if run_with_timeout "$CODEX_ADVISOR_TIMEOUT_SECONDS" "$advisor_output" \
    codex exec --sandbox read-only "Analyze this failed Helm/Kubernetes deployment. Do not change files. Give: likely root cause, evidence, exact commands to verify, and a minimal proposed fix. Helm output: $(cat /tmp/helm-error.txt) Kubernetes state: $(cat /tmp/k8s-state.txt)"; then
    advice=$(cat "$advisor_output")
  else
    advisor_rc=$?
    advice=$(cat "$advisor_output" 2>/dev/null || true)
    advice="${advice}
Codex advisor exited with status $advisor_rc after the deployment failed. Helm and Kubernetes diagnostics were still captured."
  fi
fi
printf '%s\n' "$advice" > "$ADVICE_FILE"
slack "❌ Deployment failed for $RELEASE:$IMAGE_TAG. Codex recommendation:\n${advice:0:2500}"
exit $rc
