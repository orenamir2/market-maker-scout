#!/usr/bin/env bash
set -uo pipefail
set -xv
RELEASE=${RELEASE:-market-maker-scout}
NAMESPACE=${NAMESPACE:-market-maker-scout}
IMAGE_REPOSITORY=${IMAGE_REPOSITORY:?IMAGE_REPOSITORY is required}
IMAGE_TAG=${IMAGE_TAG:?IMAGE_TAG is required}
SLACK_WEBHOOK_URL=${SLACK_WEBHOOK_URL:-}

slack(){ [ -z "$SLACK_WEBHOOK_URL" ] || curl -fsS -X POST -H 'Content-type: application/json' --data "{\"text\":$(python -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1")}" "$SLACK_WEBHOOK_URL" >/dev/null; }

slack "🚀 Deploying $RELEASE:$IMAGE_TAG to namespace $NAMESPACE"
set +e
output=$(helm upgrade --install "$RELEASE" helm/market-maker-scout --namespace "$NAMESPACE" --create-namespace --set image.repository="$IMAGE_REPOSITORY" --set image.tag="$IMAGE_TAG" --wait --timeout 5m 2>&1)
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
  advice=$(codex exec --sandbox read-only "Analyze this failed Helm/Kubernetes deployment. Do not change files. Give: likely root cause, evidence, exact commands to verify, and a minimal proposed fix. Helm output: $(cat /tmp/helm-error.txt) Kubernetes state: $(cat /tmp/k8s-state.txt)" 2>&1 || true)
fi
printf '%s\n' "$advice" > deployment-advice.txt
slack "❌ Deployment failed for $RELEASE:$IMAGE_TAG. Codex recommendation:\n${advice:0:2500}"
exit $rc
