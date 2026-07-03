"""status.json — atomic read/write. State machine home: mcp-server-reference §4."""
import json
import os
import time
from pathlib import Path

STATES = ("uninitialized", "queued", "running", "indexed", "degraded", "failed")


def write_status(path: Path, **fields) -> None:
    assert fields.get("state") in STATES, fields.get("state")
    fields["updated_at"] = time.time()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(fields))
    os.replace(tmp, path)


def read_status(path: Path) -> dict:
    if not path.exists():
        return {"state": "uninitialized"}
    return json.loads(path.read_text())
