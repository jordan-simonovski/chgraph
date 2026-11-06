import pytest

from chgraph.client import DaemonClient, DaemonError


def test_old_daemon_cannot_silently_ignore_codegraph_source(monkeypatch):
    client = DaemonClient("unused.sock")
    calls = []

    def old_daemon(op, **params):
        calls.append(op)
        return {"pong": True} if op == "ping" else {"job_id": "native-job"}

    monkeypatch.setattr(client, "call", old_daemon)
    with pytest.raises(DaemonError, match="restart"):
        client.index("codegraph")
    assert calls == ["ping"]
