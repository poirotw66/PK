"""Export initial cloud import or a version-guarded local update."""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scheduler", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.scheduler / "src"))
    from squash_scheduler.cloud_handoff import export_cloud_schedule
    from squash_scheduler.storage.repository import Repository

    payload = export_cloud_schedule(Repository(args.workspace))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    state = payload.get("state", payload)
    print(
        json.dumps(
            {
                "format": payload["format"],
                "matches": len(state["matches"]),
                "dates": state["dates"],
                "source_revision": state["source_revision"],
            }
        )
    )


if __name__ == "__main__":
    main()
