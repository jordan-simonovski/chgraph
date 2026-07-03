"""Tiny blocking JSON-lines client for the daemon socket. Used by CLI and shim.
MUST NOT import chdb (the shim imports this)."""
import json
import socket
from pathlib import Path


class DaemonError(RuntimeError):
    pass


class DaemonClient:
    def __init__(self, socket_path: Path):
        self.socket_path = Path(socket_path)

    def call(self, op: str, **params) -> dict:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(120)
                s.connect(str(self.socket_path))
                s.sendall((json.dumps({"op": op, "params": params}) + "\n").encode())
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            if not buf:
                raise DaemonError(
                    f"daemon at {self.socket_path} closed connection without a complete response")
            resp = json.loads(buf.decode())
        except OSError as e:
            raise DaemonError(f"cannot reach daemon at {self.socket_path}: {e}") from e
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise DaemonError(
                f"daemon at {self.socket_path} sent a malformed response: {e}") from e
        if not resp.get("ok"):
            raise DaemonError(resp.get("error", "unknown daemon error"))
        return resp["data"]
