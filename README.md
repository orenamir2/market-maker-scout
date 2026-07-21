# Market Maker Scout

Kubernetes-first experimental web application that ranks up to 750 tickers using statistical proxies for accumulation. It does **not** observe or prove market-maker activity and must not be treated as financial advice.

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

By default each scan fetches the latest available daily bars from the configured provider. If no custom tickers are supplied, the scanner builds a 750-symbol default universe from Nasdaq screener rows with market cap between `$500M` and `$2B`, sorted by market cap descending. Set `DATA_MODE=demo` to use deterministic synthetic data for offline development and tests. Provider bars may be delayed; use a licensed feed for production-grade real-time guarantees.

## Kubernetes
```bash
docker build -t your-registry/market-maker-scout:dev .
docker push your-registry/market-maker-scout:dev
helm upgrade --install market-maker-scout helm/market-maker-scout \
  --namespace market-maker-scout --create-namespace \
  --set image.repository=your-registry/market-maker-scout \
  --set image.tag=dev
```

## Published versions
The CI workflow publishes both artifacts to GitHub Container Registry (GHCR)
with the same version:

- Docker image: `ghcr.io/<owner>/market-maker-scout:<version>`
- Helm chart: `oci://ghcr.io/<owner>/charts/market-maker-scout --version <version>`

For pushes to `main`, the version is based on the chart version and workflow
run number, for example `0.1.0-main.42`. For Git tags like `v1.2.3`, the
published version is `1.2.3`.

To see versions in GitHub, open the repository page, choose **Packages**, then
open the `market-maker-scout` container package for Docker images or the
`charts/market-maker-scout` container package for Helm charts.

With the GitHub CLI:

```bash
OWNER=<github-user-or-org>
REPO=market-maker-scout

# Docker image tags
gh api "/users/$OWNER/packages/container/$REPO/versions" \
  --jq '.[] | .metadata.container.tags[]?'

# Helm chart versions
gh api "/users/$OWNER/packages/container/charts%2F$REPO/versions" \
  --jq '.[] | .metadata.container.tags[]?'
```

If the package is owned by a GitHub organization, replace `/users/$OWNER` with
`/orgs/$OWNER`.

Install a matching image and chart by using the same version in both places:

```bash
OWNER=<github-user-or-org>
VERSION=0.1.0-main.42

helm upgrade --install market-maker-scout \
  "oci://ghcr.io/$OWNER/charts/market-maker-scout" \
  --version "$VERSION" \
  --namespace market-maker-scout --create-namespace \
  --set image.repository="ghcr.io/$OWNER/market-maker-scout" \
  --set image.tag="$VERSION"
```

Daily scans are designed for a local cluster that may be asleep when the exact
CronJob time passes. The Helm CronJob calls `/api/daily-scan` on a regular
schedule, and the app only runs the scan when no dated scan exists for the
current `SCAN_TIMEZONE` day. This catches the next available window without
duplicating saved scans for the same date.

When `dailyScan.tickers` is left empty, the CronJob scans the default 750-symbol
`$500M`-`$2B` universe. Set `dailyScan.tickers` only when you want to override
that default with a custom list.

## Required GitHub secrets
- `KUBE_CONFIG`: base64-encoded kubeconfig with least-privilege access
- `SLACK_WEBHOOK_URL`: Slack incoming webhook
- `OPENAI_API_KEY`: Codex authentication for failure analysis

## Optional GitHub secrets
- `GHCR_PULL_TOKEN`: long-lived token with `read:packages` access for Kubernetes image pulls. If omitted, CI uses the workflow token to create the pull secret during deployment.

## Production roadmap
1. Replace prototype Yahoo Finance chart fetches with a licensed data provider supporting intraday trades/quotes and volume with explicit latency guarantees.
2. Store observations in PostgreSQL/TimescaleDB; add Redis and Celery/Arq workers for 750-symbol scans.
3. Add features: abnormal volume, VWAP behavior, OBV/CMF, block-trade proxy, spread/liquidity changes, relative strength, regime adjustment.
4. Backtest with walk-forward validation; calibrate confidence using out-of-sample precision, not distance from a heuristic score.
5. Add authentication, rate limits, audit logs, Prometheus metrics, alerts, NetworkPolicy and External Secrets.
6. Let Codex create a proposed patch/PR only in a sandbox branch; require human review before deployment.
