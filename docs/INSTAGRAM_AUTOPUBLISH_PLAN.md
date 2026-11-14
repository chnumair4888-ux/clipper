# Instagram Reels Auto-Publishing — Integration Plan

## Status

Planning/documentation only. This change does not publish anything and does not contain Meta credentials. The existing n8n Instagram API credential must be selected inside the user's n8n instance before a live workflow can run.

## Target flow

1. Receive a completed clip upload event from the clipper.
2. Verify the clip is marked successful by export validation and has a stable public HTTPS URL.
3. Wait for the configured publishing delay (default proposal: 60 minutes; make this configurable in n8n).
4. Check that the media URL is reachable by Meta without login, cookies, or a temporary browser session.
5. Create an Instagram media container using the Instagram Graph API:
   - `POST /{ig-user-id}/media`
   - `media_type=REELS`
   - `video_url=<public-https-mp4-url>`
   - `caption=<caption>`
   - optional `share_to_feed=true`
6. Poll the returned container ID's `status_code` until it is `FINISHED`; stop and report `ERROR` or timeout rather than publishing an unfinished container.
7. Publish exactly once:
   - `POST /{ig-user-id}/media_publish`
   - `creation_id=<finished-container-id>`
8. Store the returned media ID, source clip ID, caption, timestamp, and final status for audit and duplicate prevention.
9. Notify the dashboard/n8n execution log of success or a useful failure reason.

## Required n8n configuration

- Existing Instagram Graph API credential selected on the HTTP Request nodes; never paste access tokens into workflow JSON, Git, logs, or dashboard code.
- Instagram professional account ID configured as an environment variable or protected n8n variable. Do not hardcode a credential or assume the account ID in this repository is still current.
- Graph API version pinned to the version supported by the connected Meta app.
- Public HTTPS video hosting that Meta's servers can fetch directly.
- A persistent store (n8n Data Store or database) for idempotency records.
- Error workflow/notification destination configured by the account owner.

## Safety and reliability rules

- Only publish clips whose export quality report says validation passed.
- Reject missing, non-HTTPS, private, expired, or non-MP4 media URLs.
- Enforce Instagram's current Reels media requirements at runtime; do not assume every rendered duration or aspect ratio is accepted.
- Poll container status with a bounded retry interval and overall timeout.
- Never call `media_publish` until the container is `FINISHED`.
- Use an idempotency key based on the clip's stable ID. If a publish request times out, check stored state before retrying to avoid duplicate Reels.
- Redact tokens and authorization headers from all error logs.
- Keep publishing disabled until a manual test Reel succeeds and the account owner confirms the result.
- Do not automatically retry permanent Meta errors (permission, policy, unsupported media, invalid account) without human review.

## Acceptance test checklist

- [ ] A valid, publicly reachable MP4 creates a media container.
- [ ] Container status is polled until `FINISHED`.
- [ ] A finished container publishes exactly one Reel.
- [ ] A failed container is not published.
- [ ] A private/expired URL is rejected before the API call.
- [ ] A duplicate clip event does not create a second Reel.
- [ ] A timeout after publish is reconciled by checking stored state before retry.
- [ ] Tokens are absent from logs and exported workflow JSON.
- [ ] The published Reel is visible on the connected Instagram account.
- [ ] Failure and success notifications reach the configured destination.

## Deployment boundary

The GitHub repository connector can prepare and review this integration plan, but it cannot access the running n8n editor or select the existing private credential. The workflow must be imported/configured in n8n (or deployed through an authorized n8n API connection) before any live Instagram publishing can be tested. No live publish is performed by this repository change.
