# PK — Squash tournament scheduler

This repository contains the Python scheduler/operator console and the spectator
reader source. It starts with a fresh Git history. Original rosters, player names,
PDFs, spreadsheets, event workspaces, public snapshots, production identities,
credentials, build output, and operational history are not included.

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
