# qapal/scan — GitHub Action

AI-powered accessibility, SEO, performance, and WCAG compliance scanning for your CI/CD pipeline.

## Usage

```yaml
# .github/workflows/qapal.yml
name: QAPAL Quality Scan

on:
  pull_request:
    branches: [main, staging]
  push:
    branches: [main]

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: QAPAL Scan
        uses: qapal/scan@v1
        with:
          url: ${{ vars.STAGING_URL }}
          token: ${{ secrets.QAPAL_TOKEN }}
          fail_on: major          # fail the build on major+ issues
```

## With Deep Scan (AI behavioral testing)

```yaml
      - name: QAPAL Deep Scan
        uses: qapal/scan@v1
        with:
          url: ${{ vars.STAGING_URL }}
          token: ${{ secrets.QAPAL_TOKEN }}
          prd: tests/acceptance.md   # plain-English test spec
          fail_on: critical
          timeout: '600'
```

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `url` | ✅ | — | URL to scan (use your staging environment URL) |
| `token` | ✅ | — | QAPAL API token (`Settings → Secrets → QAPAL_TOKEN`) |
| `fail_on` | ❌ | `major` | Minimum severity that fails the build: `critical` / `major` / `medium` / `minor` / `none` |
| `prd` | ❌ | — | Path to a plain-English test specification (.md). Triggers Deep Scan. |
| `backend_url` | ❌ | `https://api.qapal.dev` | Override for self-hosted deployments |
| `poll_interval` | ❌ | `5` | Polling interval in seconds |
| `timeout` | ❌ | `300` | Maximum scan wait time in seconds |

## Outputs

| Output | Description |
|---|---|
| `score` | Overall scan score (0–100) |
| `issues_count` | Total issues found |
| `critical_count` | Number of critical issues |
| `report_url` | URL to the full report |
| `job_id` | QAPAL job ID for this run |

## Why `fail_on: major` (not `critical`)?

`critical` means WCAG Level A violations — things like missing form labels and missing image alt text. Nearly every real-world site has at least one of these. Using `fail_on: critical` out of the box would fail your CI on the first run and frustrate developers.

`major` means serious but not Level-A violations. Start here, fix the criticals, then tighten to `critical` once your baseline is clean.

## Severity levels

| Level | Examples |
|---|---|
| `critical` | Missing form label, missing alt text (WCAG Level A) |
| `major` | Low contrast ratio, missing landmark, invalid ARIA role |
| `medium` | Missing meta description, wrong input type, heading skip |
| `minor` | Missing lazy loading, meta description too long |

## Getting a token

1. Sign up at [qapal.dev](https://qapal.dev)
2. Go to Settings → API Tokens → Create Token
3. Add it as a repository secret: `Settings → Secrets and variables → Actions → QAPAL_TOKEN`

## Self-hosted deployment

```yaml
      - name: QAPAL Scan (self-hosted)
        uses: qapal/scan@v1
        with:
          url: https://staging.myapp.com
          token: ${{ secrets.QAPAL_TOKEN }}
          backend_url: https://qapal.internal.mycompany.com
```
