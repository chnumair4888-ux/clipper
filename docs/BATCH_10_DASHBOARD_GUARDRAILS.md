# Batch 10 — Dashboard input guardrails

## Changes
- Limits the dashboard clip-count input to 1–10, matching the current clipper input contract.
- Validates optional time ranges as `start-end` minutes and rejects an end time that is not greater than the start.
- Makes the dashboard's success message precise: an n8n webhook acknowledgement confirms receipt only; it does not prove GitHub Actions started.
- Adds a visible note that live render progress and Instagram publishing status are not yet displayed.
- Extends the automated workflow contract tests to cover these dashboard rules.

## Status and limitations
This is a dashboard validation and messaging improvement, not live job tracking. The current webhook response is configured to acknowledge receipt immediately. Real job status requires an authenticated status endpoint or a callback/store that correlates the n8n submission with a GitHub Actions run ID. No such endpoint is available to this repository-only change, so the dashboard explicitly directs the user to GitHub Actions for confirmation.

## Verification
The tests are wired into the GitHub Actions workflow and will run before processing. They have not yet been executed in this editing session. No real video, Oracle upload, or Instagram publication was performed.
