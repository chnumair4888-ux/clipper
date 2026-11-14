# Final Integration Test Plan

This checklist is for the final combined test after the stacked pull requests have been reviewed and integrated in order. Static review is not a substitute for these tests.

## 1. Before merging or running

- Review the Oracle deployment workflow separately. It must remain disabled until its SSH secrets and host fingerprint are independently configured and verified.
- Confirm GitHub Actions secrets exist: `GEMINI_API_KEY`, `ORACLE_UPLOAD_URL`, and `ORACLE_UPLOAD_TOKEN`. Add `YT_COOKIES` only when needed for a source that requires authenticated access.
- Confirm the Oracle upload service is reachable and has sufficient disk space.
- Confirm the n8n webhook and GitHub dispatch credential still work. A webhook's immediate acknowledgement alone does not prove that GitHub Actions started.
- Use a video that you own or have permission to edit and publish.

## 2. Merge order

The changes are stacked. Review and integrate in this order; do not merge a later batch before its base branch is integrated:

1. PR #2 — professional editing
2. PR #3 — karaoke captions
3. PR #4 — music and audio modes
4. PR #5 — export validation
5. PR #6 — dashboard and workflow updates

PR #1 (Oracle deployment) is separate and should be reviewed independently. It is not required to run the existing GitHub-hosted clipping workflow.

## 3. Rendering matrix

Run a short representative source video first, then a longer source after the first pass succeeds.

- Vertical + blur background + word captions.
- Vertical + crop background + line captions.
- Horizontal + captions disabled.
- Both orientations.
- Roman Urdu transcription/captions.
- Each editing preset: auto, viral, cinematic, funny, esports.
- Original audio, commentary, music-only, and original-plus-music.
- Missing music folder/track: the clip should retain its existing audio and report that no track was found.
- Music track present: verify the track's license allows the intended platform and monetized use before testing it.

## 4. Output checks

For every successful output, verify:

- Video dimensions are exactly 1080x1920 (vertical) or 1920x1080 (horizontal).
- H.264 video, yuv420p pixel format, and a playable MP4.
- Duration is close to the selected clip duration.
- Captions are synchronized, legible on a phone, and not hidden by platform UI.
- Audio is clear, not clipped, and narration/music levels are balanced.
- `out/quality_report.json` contains a passed record for each successful clip.
- Failed quality checks do not leave MP4 files in the upload directory.
- Oracle receives only validated MP4 files and the public file links open successfully.

## 5. End-to-end dashboard check

- Submit one test job from the dashboard.
- Confirm n8n receives the request and GitHub Actions dispatches the intended workflow.
- Confirm the Actions run completes and its logs contain no exposed secrets.
- Confirm the resulting files and metadata arrive on Oracle.
- Test the dashboard's error path with a deliberately invalid test input.

## 6. Instagram publishing

Automated Instagram publishing is a separate integration task. It must not be treated as complete until the n8n publishing workflow is configured with the existing authorized Meta credential, the correct Instagram professional account, and a successful test publication. Never place access tokens in this repository or in chat.

## Test record

Record the source video used, selected inputs, Actions run URL, output filenames, quality report, upload URLs, and any issues found. Do not mark the system production-ready until the full path has passed.
