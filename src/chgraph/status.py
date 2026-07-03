"""status.json — atomic read/write. State machine home: mcp-server-reference §4."""
import json
import os
import tempfile
import time
from pathlib import Path

STATES = ("uninitialized", "queued", "running", "indexed", "degraded", "failed")


def write_status(path: Path, **fields) -> None:
    assert fields.get("state") in STATES, fields.get("state")
    fields["updated_at"] = time.time()
    # A per-call unique temp name (not a fixed shared ".tmp" path) avoids concurrent
    # writers corrupting each other's temp file or racing os.replace into a
    # FileNotFoundError. Same directory as the target keeps the rename atomic.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(fields))
            f.flush()
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_status(path: Path) -> dict:
    if not path.exists():
        return {"state": "uninitialized"}
    return json.loads(path.read_text())
