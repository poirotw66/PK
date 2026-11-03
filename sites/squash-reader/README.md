# Spectator reader source

The HTML/CSS/JavaScript reader and signed-sync Worker are included as source.
No real event snapshots, PDF files, deployment identity, or Worker bundle is tracked.

From this directory, with the scheduler installed:

```sh
../../squash-scheduler/.venv/bin/python build.py --scheduler ../../squash-scheduler --workspace ../../squash-scheduler/output/anonymous-demo/tournament
../../squash-scheduler/.venv/bin/python scripts/build-worker.py
node --test tests/reader-order.test.mjs tests/sync-worker.test.mjs
node scripts/preview-sync.mjs
```

Use the actual workspace printed by the demo. All output goes to ignored `dist/`.
An optional local `public-documents/manifest.json` can supply a PDF: `filename`,
`sha256`, `pages`, `exported_at`, `revision_id`, and `status`. Without it the build
omits PDF controls. Never commit that directory or production event data.
Set your own local `.openai/hosting.json` before deployment. macOS background sync
scripts are optional and require a local host setting and Keychain credential.

## Cloud court updates

The reader accepts `/sync/cloud-publish` only with the dedicated Sites runtime secret `CLOUD_PUBLISH_SECRET` and matching `PUBLISH_SOURCE_ID`. Messages are HMAC signed over timestamp, endpoint path, and exact JSON bytes. The cloud envelope contains an explicit public field allowlist and is merged into the existing public snapshot without replacing participant identities, bracket metadata, standings, or scores.

A monotonic source sequence and conditional R2 pointer prevent replay and concurrent overwrite. After the first accepted cloud message, legacy `/sync/update` and `/sync/heartbeat` requests cannot replace the cloud-controlled state. Deploying this code alone does not change the current published schedule or start matches.

The prominent current-court area displays all courts independently of search filters. Scheduled time and administrator-confirmed actual start are distinct. The existing ten-second polling retrieves updates; connection failures retain the last successful data.

`node --test tests/*.test.mjs` checks the built Worker. Local preview supports `PORT` (default 8780) and an optional `PREVIEW_SNAPSHOT` file for matching synthetic integration fixtures. Preview secrets are test-only literals and are never used in production.

## Feature pages and automatic standings

The reader has separate `/live`, `/schedule`, `/brackets` and `/standings` pages,
with mobile navigation and backwards-compatible shared links. Active-court scores
refresh through a separate two-second read-only channel. Personal schedule changes
remain highlighted until acknowledged.

Confirmed cloud results automatically rebuild affected pool points, wins, losses,
game totals and numeric ranks. Incomplete pools show provisional ranks. Two-player
ties use head-to-head, then game difference and point difference; unresolved ties
share a numeric rank. Complete pools without unresolved ties finish automatically.
Stage rules are exported from the synthetic workspace, shared by Worker and reader.
Repeated publications do not accumulate statistics. Existing non-cloud manual
decisions are preserved; assigning later-round players is a separate operation.

Run all reader checks with `node --test tests/*.test.mjs` after building.
