# Getting Started with QAPAL

QAPAL is a CI-native system that validates real user flows using AI. It catches broken experiences before your users do.

## 1. Quick Install (GitHub Action)

Add this to your `.github/workflows/e2e.yml`:

```yaml
name: QAPAL E2E Scan
on: [pull_request]

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run QAPAL Scan
        uses: qapal/action@v1
        with:
          url: "https://staging.myapp.com"
          token: ${{ secrets.QAPAL_TOKEN }}
          test_user: ${{ secrets.STAGING_USER }}
          test_pass: ${{ secrets.STAGING_PASS }}
          prd: "tests/smoke_test.md" # Path to your user flow spec
```

## 2. Definining User Flows

Create a `.md` file (e.g., `tests/smoke_test.md`) describing what you want QAPAL to test. Use plain English:

- **Navigate** to `/login`
- **Fill** email and password
- **Click** "Sign In"
- **Verify** dashboard is visible

## 3. The "Aha Moment"

When a test fails in your PR, QAPAL will:
1.  **Surgically Repair**: Automatically try to fix the step (e.g., if a selector changed).
2.  **Report to PR**: Post a Markdown summary with screenshots and failure reasons.
3.  **Reproduce Locally**: Give you a downloadable `reproduce_test.ts` file that you can run with standard Playwright.

## 4. Troubleshooting
If QAPAL fails to find an element, it triggers the **Surgical Healer**. You'll see "Healed" annotations in your PR report if the AI successfully bypassed a UI breakage.
