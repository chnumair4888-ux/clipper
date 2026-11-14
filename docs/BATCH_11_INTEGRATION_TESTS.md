# Batch 11 — n8n / GitHub integration contract checks

## What this batch adds

- Tests the exported n8n workflow JSON parses correctly.
- Checks the dashboard webhook path and POST method match the n8n webhook.
- Checks the n8n GitHub dispatch endpoint, HTTP method, and credential type.
- Checks every supported GitHub Actions input is mapped by the n8n dispatch expression.
- Confirms the template dispatches `main`, contains no obvious pasted access token, and remains inactive until configured.

## Important integration finding

The n8n template dispatches the `main` branch. This is intentional: the stacked feature branches must be reviewed and integrated in order before a real dashboard-to-GitHub test can exercise all changes. Do not point the live n8n workflow at an unreviewed feature branch as a shortcut.

The webhook is configured for immediate acknowledgement (`onReceived`). A successful dashboard response therefore means n8n received the request; it does not prove the GitHub dispatch succeeded or that a render started. The current dashboard explains this limitation.

## Verification boundary

These are static contract tests. They do not call the live n8n instance, authenticate to GitHub, process a video, upload to Oracle, or publish to Instagram. Those require the final controlled integration run with the account owner's configured credentials.

## Next live-test sequence

1. Review and integrate the stacked PRs in dependency order.
2. Run the GitHub Actions contract tests on the integrated branch.
3. Confirm the n8n workflow is active and its GitHub credential is selected in n8n.
4. Submit a short video that the account owner is allowed to process.
5. Confirm the GitHub Actions run, rendered MP4s, quality report, and Oracle public URLs.
6. Test Instagram publishing separately only after the n8n Meta credential and public video URL checks are configured.
