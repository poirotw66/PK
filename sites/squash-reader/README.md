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
