"""The daemon: sole owner of the chdb Session (INV-1). One project per daemon.
Protocol: newline-delimited JSON over a unix socket. Lifecycle spec: chgraph-run-and-operate §2."""
import asyncio
import dataclasses
import json
import logging
import os
import queue
import sys
import threading
import uuid
from concurrent.futures import Future
from pathlib import Path

from chgraph import search as search_mod
from chgraph import traverse as traverse_mod
from chgraph.evolution import _q
from chgraph.indexer import index_repository
from chgraph.paths import ProjectPaths, project_slug
from chgraph.status import read_status, write_status
from chgraph.store import Store

log = logging.getLogger("chgraph.daemon")


class SessionWorker:
    """ALL chdb access goes through this single thread.
    ponytail: global serialization; per-op scheduling only if throughput ever demands it
    (Session thread-safety is OPEN — chgraph-architecture-contract weak points)."""

    def __init__(self, store: Store):
        self._store = store
        self._q: queue.Queue = queue.Queue()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        while True:
            fn, fut = self._q.get()
            if fn is None:
                fut.set_result(None)
                return
            try:
                fut.set_result(fn(self._store))
            except Exception as e:                     # noqa: BLE001 — daemon must not die on a bad query
                fut.set_exception(e)

    def submit(self, fn) -> Future:
        fut: Future = Future()
        self._q.put((fn, fut))
        return fut

    def stop(self):
        fut: Future = Future()
        self._q.put((None, fut))
        fut.result(timeout=30)
        self._store.close()


class Daemon:
    def __init__(self, repo_root: str):
        self.repo_root = os.path.realpath(repo_root)
        self.project = project_slug(self.repo_root)
        self.paths = ProjectPaths.for_repo(self.repo_root)
        self.paths.ensure()
        self.worker = SessionWorker(Store.open(self.paths.chdb_dir))
        self._index_thread: threading.Thread | None = None
        self._stop = asyncio.Event()

    # ---- index job (runs in its own thread; only DB calls go through the worker) ----
    def _index_job(self, job_id: str):
        sp = self.paths.status_json
        try:
            write_status(sp, state="running", repo_root=self.repo_root, job_id=job_id,
                         files_total=0, files_done=0, nodes_persisted=0,
                         degraded_reasons=[], error=None)

            def progress(done, total):
                write_status(sp, state="running", repo_root=self.repo_root, job_id=job_id,
                             files_total=total, files_done=done, nodes_persisted=0,
                             degraded_reasons=[], error=None)

            res = self.worker.submit(
                lambda st: index_repository(st, self.project, self.repo_root, progress)
            ).result()
            state = "degraded" if res.degraded_reasons else "indexed"
            write_status(sp, state=state, repo_root=self.repo_root, job_id=job_id,
                         files_total=res.files_total, files_done=res.files_done,
                         nodes_persisted=res.nodes, degraded_reasons=res.degraded_reasons,
                         error=None)
        except Exception as e:                          # noqa: BLE001
            log.exception("index job failed")
            write_status(sp, state="failed", repo_root=self.repo_root, job_id=job_id,
                         files_total=0, files_done=0, nodes_persisted=0,
                         degraded_reasons=[], error=str(e))

    # ---- ops ----
    def op_ping(self, **_):
        return {"pong": True, "project": self.project, "pid": os.getpid()}

    def op_index(self, **_):
        if self._index_thread and self._index_thread.is_alive():
            return {"job_id": read_status(self.paths.status_json).get("job_id"),
                    "state": "running"}
        job_id = f"idx-{uuid.uuid4().hex[:8]}"
        write_status(self.paths.status_json, state="queued", repo_root=self.repo_root,
                     job_id=job_id, files_total=0, files_done=0, nodes_persisted=0,
                     degraded_reasons=[], error=None)
        self._index_thread = threading.Thread(target=self._index_job, args=(job_id,), daemon=True)
        self._index_thread.start()
        return {"job_id": job_id, "state": "queued"}

    def op_status(self, **_):
        return read_status(self.paths.status_json)     # never touches chdb — stays responsive

    def op_search(self, **params):
        page = self.worker.submit(
            lambda st: search_mod.search_graph(st, self.project, **params)).result()
        return dataclasses.asdict(page)

    def op_snippet(self, qualified_name: str, **_):
        row = self.worker.submit(lambda st: st.rows(
            f"SELECT file_path, start_line, end_line FROM chgraph.nodes FINAL "
            f"WHERE project = {_q(self.project)} AND qualified_name = {_q(qualified_name)}"
        )).result()
        if not row:
            raise ValueError(f"unknown symbol: {qualified_name}")
        r = row[0]
        lines = open(os.path.join(self.repo_root, r["file_path"]), errors="replace").read().splitlines()
        text = "\n".join(lines[r["start_line"] - 1:r["end_line"]])
        return {"qualified_name": qualified_name, "file_path": r["file_path"],
                "start_line": r["start_line"], "end_line": r["end_line"], "text": text}

    def op_trace(self, qualified_name: str, direction: str = "callees", depth: int = 5, **_):
        rows = self.worker.submit(lambda st: traverse_mod.trace_path(
            st, self.project, qualified_name, direction=direction, depth=depth)).result()
        return {"paths": rows}

    def op_schema_info(self, **_):
        def q(st):
            labels = [r["label"] for r in st.rows(
                f"SELECT DISTINCT label FROM chgraph.nodes FINAL WHERE project = {_q(self.project)} ORDER BY label")]
            etypes = [r["type"] for r in st.rows(
                f"SELECT DISTINCT type FROM chgraph.edges FINAL WHERE project = {_q(self.project)} ORDER BY type")]
            return {"labels": labels, "edge_types": etypes}
        return self.worker.submit(q).result()

    def op_list_projects(self, **_):
        return {"projects": [{"project": self.project, "repo_root": self.repo_root,
                              "state": read_status(self.paths.status_json)["state"]}]}

    def op_delete_project(self, **_):
        def wipe(st):
            for t in ("nodes", "edges", "git_commits", "git_file_changes",
                      "file_evolution", "embeddings"):
                st.exec(f"TRUNCATE TABLE chgraph.{t}")
        self.worker.submit(wipe).result()
        write_status(self.paths.status_json, state="uninitialized", repo_root=self.repo_root,
                     job_id=None, files_total=0, files_done=0, nodes_persisted=0,
                     degraded_reasons=[], error=None)
        return {"deleted": self.project}

    # ---- server ----
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            line = await reader.readline()
            if not line:
                return
            req = json.loads(line.decode())
            op = req.get("op", "")
            handler = getattr(self, f"op_{op}", None)
            if op == "shutdown":
                writer.write(b'{"ok": true, "data": {"bye": true}}\n')
                await writer.drain()
                self._stop.set()
                return
            if handler is None:
                resp = {"ok": False, "error": f"unknown op: {op}"}
            else:
                loop = asyncio.get_running_loop()
                try:
                    data = await loop.run_in_executor(
                        None, lambda: handler(**req.get("params", {})))
                    resp = {"ok": True, "data": data}
                except Exception as e:                  # noqa: BLE001
                    resp = {"ok": False, "error": str(e)}
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()

    async def serve(self):
        if self.paths.socket.exists():
            self.paths.socket.unlink()                 # stale socket from a crash
        server = await asyncio.start_unix_server(self.handle, path=str(self.paths.socket))
        self.paths.pidfile.write_text(f"{os.getpid()}\n")
        if not self.paths.status_json.exists():
            write_status(self.paths.status_json, state="uninitialized",
                         repo_root=self.repo_root, job_id=None, files_total=0,
                         files_done=0, nodes_persisted=0, degraded_reasons=[], error=None)
        log.info("chgraph daemon up: project=%s socket=%s", self.project, self.paths.socket)
        async with server:
            await self._stop.wait()
        self.worker.stop()
        for p in (self.paths.socket, self.paths.pidfile):
            p.unlink(missing_ok=True)


def run_daemon(repo_root: str) -> None:
    paths = ProjectPaths.for_repo(repo_root)
    paths.ensure()
    logging.basicConfig(
        filename=paths.log_dir / "daemon.log", level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        d = Daemon(repo_root)
    except Exception as e:                              # noqa: BLE001
        # Code 36/76 pair == another process owns the chdb dir (run-and-operate §2).
        msg = str(e)
        if "EmbeddedServer" in msg or "Cannot lock file" in msg:
            print(f"another chgraph daemon (or stray process) owns {paths.chdb_dir}; "
                  f"run `chgraph daemon status`", file=sys.stderr)
            raise SystemExit(1)
        raise
    asyncio.run(d.serve())


if __name__ == "__main__":
    run_daemon(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())
