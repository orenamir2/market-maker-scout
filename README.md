# Market Maker Scout

Kubernetes-first experimental web application that ranks up to 250 tickers using statistical proxies for accumulation. It does **not** observe or prove market-maker activity and must not be treated as financial advice.

## Architecture
- FastAPI backend and simple browser UI
- Pluggable market-data/scoring layer with live Yahoo Finance chart fetches by default (demo data mode still available)
- Docker image and Helm chart
- GitHub Actions CI/CD
- Slack deployment notifications
- Codex CLI failure advisor that inspects Helm output and Kubernetes diagnostics, then proposes a fix without changing production automatically

## Local run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open http://localhost:8000.

By default each scan fetches the latest available daily bars from the configured provider. Set `DATA_MODE=demo` to use deterministic synthetic data for offline development and tests. Provider bars may be delayed; use a licensed feed for production-grade real-time guarantees.

## Kubernetes
```bash
docker build -t your-registry/market-maker-scout:dev .
docker push your-registry/market-maker-scout:dev
helm upgrade --install market-maker-scout helm/market-maker-scout \
  --namespace market-maker-scout --create-namespace \
  --set image.repository=your-registry/market-maker-scout \
  --set image.tag=dev
```

Daily scans are designed for a local cluster that may be asleep when the exact
CronJob time passes. The Helm CronJob calls `/api/daily-scan` on a regular
schedule, and the app only runs the scan when no dated scan exists for the
current `SCAN_TIMEZONE` day. This catches the next available window without
duplicating saved scans for the same date.

## Required GitHub secrets
- `KUBE_CONFIG`: base64-encoded kubeconfig with least-privilege access
- `SLACK_WEBHOOK_URL`: Slack incoming webhook
- `OPENAI_API_KEY`: Codex authentication for failure analysis

## Optional GitHub secrets
- `GHCR_PULL_TOKEN`: long-lived token with `read:packages` access for Kubernetes image pulls. If omitted, CI uses the workflow token to create the pull secret during deployment.

## Production roadmap
1. Replace prototype Yahoo Finance chart fetches with a licensed data provider supporting intraday trades/quotes and volume with explicit latency guarantees.
2. Store observations in PostgreSQL/TimescaleDB; add Redis and Celery/Arq workers for 250-symbol scans.
3. Add features: abnormal volume, VWAP behavior, OBV/CMF, block-trade proxy, spread/liquidity changes, relative strength, regime adjustment.
4. Backtest with walk-forward validation; calibrate confidence using out-of-sample precision, not distance from a heuristic score.
5. Add authentication, rate limits, audit logs, Prometheus metrics, alerts, NetworkPolicy and External Secrets.
6. Let Codex create a proposed patch/PR only in a sandbox branch; require human review before deployment.
