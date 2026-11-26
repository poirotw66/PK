# PK — Squash tournament scheduler

This repository contains the Python scheduler/operator console and the spectator
reader source. It starts with a fresh Git history. Original rosters, player names,
PDFs, spreadsheets, event workspaces, public snapshots, production identities,
credentials, build output, and operational history are not included.

## Local preparation and cloud operation

The local interface now opens an empty workspace with a guided setup flow for
Excel (`.xlsx`), Word (`.docx`), and text PDFs. Review names, shared player
identities, divisions, event settings, and rules before creating the event.
Use the existing operator console for formats, approved draws, scheduling, and
document exports. Cloud Sites continue to handle live operation and public scores.

With Python 3 and uv installed, run:

```sh
python3 scripts/start_local.py
```

Open `http://127.0.0.1:8765/`. Windows users can run `START-LOCAL.cmd`;
Linux/macOS users can run `sh START-LOCAL.sh`. Use a fresh workspace for another
event: `python3 scripts/start_local.py --workspace squash-scheduler/tournaments/new-event`.
The first run downloads the locked dependencies and Python 3.13 if needed.

The `/setup` page exports an initial cloud schedule and reviews cloud return files.
The updated administration source adds primary-administrator download and guarded
re-import of a local replan. Updating this repository does not deploy those changes
to an existing Site. See the [local-first workflow](squash-scheduler/docs/local-first-workflow.md)
for the file contracts, current cloud rule profile, and verification commands.

## Live sites

- [Tournament administration](https://pk-squash-admin.poirotw66.chatgpt.site/) — requires an authorized ChatGPT account.
- [Manual iPad scoring](https://pk-squash-admin.poirotw66.chatgpt.site/ipad) — requires an authorized ChatGPT account.
- [Public schedule and scores](https://pk-squash-schedule.poirotw66.chatgpt.site/schedule) — no sign-in required.

## Run an anonymous demo

Requires Python 3.13 and uv:

```sh
cd squash-scheduler
uv sync --frozen --extra dev
uv run python -c "from pathlib import Path; from examples.run_demo import run_demo; run_demo(Path('output/anonymous-demo'), pdf=False)"
uv run python -m squash_scheduler serve --workspace output/anonymous-demo/tournament --port 8766
```

Use the workspace path printed by the demo if it differs. Open
`http://127.0.0.1:8766/` for the operator and `/live/` for the reader.
All demo participants are explicitly synthetic. Generated files remain ignored.

## Privacy checks

```sh
python3 scripts/check_privacy.py --staged
python3 scripts/check_privacy.py --history
python3 scripts/install_privacy_hooks.py --roster /path/to/private/working/current.json
```

The hooks require the local protected-name dictionary, inspect staged Git blobs
before commits, and inspect all reachable history before pushes. The roster and
its path are never committed; the path stays in `.git/config`. Checks report file
paths and error categories without printing matching names or credentials.
CI performs structural and secret-pattern checks without the private dictionary.
These checks complement review; they cannot identify every possible personal
name or obfuscated secret automatically.

The original production Sites repository and its history are intentionally kept
separate. Never push or merge that history into this repository. Do not force-add
operational documents or generated files. Historical-source tests require local
private inputs and are not part of the portable CI selection.

## Reader source

See [reader instructions](sites/squash-reader/README.md). Runtime deployment
configuration and sync credentials must be supplied locally for your own site.

本輪成績更正、晉級、備份、地端匯入與完整驗證說明見 [專案優化紀錄](squash-scheduler/docs/project-optimization-20261125.md)。
