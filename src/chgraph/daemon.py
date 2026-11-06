"""The daemon: sole owner of the chdb Session (INV-1). One project per daemon.
Protocol: newline-delimited JSON over a unix socket. Lifecycle spec: chgraph-run-and-operate §2."""
import asyncio
import concurrent.futures
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
from chgraph import context as context_mod
from chgraph.codegraph_import import prepare_codegraph, write_snapshot
from chgraph.evolution import _q, refresh_file_evolution
from chgraph.generations import active_project, publish_generation
from chgraph.gitingest import GitIngestCounts, _batch_load, parse_git_log, verify_git_counts
from chgraph.indexer import IndexResult, index_repository
from chgraph.paths import ProjectPaths, project_slug, read_source
from chgraph.status import read_status, write_status
from chgraph.store import Store

log = logging.getLogger("chgraph.daemon")


class SessionWorker:
    """ALL chdb access goes through this single thread, which is also the sole
    owner of the chdb Session's lifetime (INV-1): it opens the Store as the first
    thing it does and closes it as the last thing it does, so the Session is never
    touched from any other thread (Session thread-safety is OPEN —
    chgraph-architecture-contract weak points).
    ponytail: global serialization; per-op scheduling only if throughput ever demands it."""

    def __init__(self, chdb_dir: str | Path, *, project: str | None = None):
        self._chdb_dir = chdb_dir
        self._project = project
        self._q: queue.Queue = queue.Queue()
        # Signals the outcome of Store.open() back to whoever constructed us,
        # so open errors ("Cannot lock file", "EmbeddedServer already initialized")
        # still surface synchronously even though open() now runs on this thread.
        self._open_result: Future = Future()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def wait_ready(self) -> None:
        """Block until the worker thread has opened the Store; re-raises the
        open exception (if any) in the calling thread."""
        self._open_result.result()

    def _run(self):
        try:
            store = Store.open(self._chdb_dir, durable_uri=os.environ.get("CHGRAPH_DURABLE_URI"),
                               project=self._project)
        except Exception as e:                          # noqa: BLE001 — surfaced via wait_ready()
            self._open_result.set_exception(e)
            return
        self._open_result.set_result(None)
        try:
            while True:
                fn, fut = self._q.get()
                if fn is None:
                    fut.set_result(None)
                    return
                try:
                    fut.set_result(fn(store))
                except Exception as e:                  # noqa: BLE001 — daemon must not die on a bad query
                    fut.set_exception(e)
        finally:
            # Always release the chdb dir lock when this thread exits, whether via
            # the stop sentinel or an unexpected error in the loop above.
            try:
                store.close()
            except Exception:                           # noqa: BLE001 — best-effort on shutdown
                log.exception("SessionWorker: failed to close store")

    def submit(self, fn) -> Future:
        fut: Future = Future()
        self._q.put((fn, fut))
        return fut

    def stop(self):
        fut: Future = Future()
        self._q.put((None, fut))
        try:
            fut.result(timeout=30)
            # The sentinel is acknowledged before finally releases the store's writer lease.
            self._t.join()
        except concurrent.futures.TimeoutError:
            # The worker didn't drain in time (e.g. stuck on a long-running query).
            # The sentinel stays queued; the worker thread will still close the
            # store itself once it gets there (see _run's finally). Log and move on
            # so the caller (serve()) can still unlink the socket/pidfile.
            log.error("SessionWorker.stop: timed out after 30s waiting for the "
                      "worker to drain; store close is pending on the worker thread")


class Daemon:
    def __init__(self, repo_root: str):
        self.repo_root = os.path.realpath(repo_root)
        self.project = project_slug(self.repo_root)
        self.paths = ProjectPaths.for_repo(self.repo_root)
        self.paths.ensure()
        self.worker = SessionWorker(self.paths.chdb_dir, project=self.project)
        # Blocks until the worker thread has opened the Store; re-raises the open
        # error (e.g. "Cannot lock file", "EmbeddedServer already initialized") here
        # on the main thread so run_daemon's except clause still sees it (INV-1: F).
        self.worker.wait_ready()
        self._index_thread: threading.Thread | None = None
        self._index_lock = threading.Lock()
        self._index_cancel = threading.Event()
        self._stop = asyncio.Event()

    # ---- index job (runs in its own thread; only DB calls go through the worker) ----
    def _index_job(self, job_id: str, source: str):
        sp = self.paths.status_json
        try:
            write_status(sp, state="running", repo_root=self.repo_root, job_id=job_id,
                         files_total=0, files_done=0, nodes_persisted=0,
                         degraded_reasons=[], error=None, source=source, experimental=source == "codegraph")

            def progress(done, total):
                write_status(sp, state="running", repo_root=self.repo_root, job_id=job_id,
                             files_total=total, files_done=done, nodes_persisted=0,
                             degraded_reasons=[], error=None, source=source, experimental=source == "codegraph")

            generation = f"{self.project}@{uuid.uuid4().hex}"
            rejected = 0
            if source == "codegraph":
                # Extraction and source validation leave the database worker free for reads.
                snapshot = prepare_codegraph(self.repo_root, cancel=self._index_cancel)
                commits, changes = parse_git_log(self.repo_root, generation)
                if self._index_cancel.is_set():
                    raise ValueError("indexing cancelled during shutdown")
                counts = GitIngestCounts(len(commits), len(changes), sum(c["is_rename"] for c in changes))
                reasons = verify_git_counts(self.repo_root, counts)
                rejected = len(snapshot["rejected_edges"])
                if rejected:
                    reasons.append(f"quarantined {rejected} unsafe/unverified Python CALLS records; evidence retained")
                reasons.extend(f"{path}: {message}" for path, message in snapshot["validation_errors"].items())

                def load(st):
                    loaded = write_snapshot(st, generation, snapshot)
                    _batch_load(st, "chgraph.git_commits", commits)
                    _batch_load(st, "chgraph.git_file_changes", changes)
                    refresh_file_evolution(st, generation, 1)
                    for table, expected in (("nodes", loaded["nodes"]), ("edges", loaded["relationships"])):
                        actual = st.rows(f"SELECT count() AS n FROM chgraph.{table} FINAL WHERE project={_q(generation)}")[0]["n"]
                        if actual != expected:
                            raise ValueError(f"incomplete {table}: persisted {actual}, expected {expected}")
                    reasons.extend(publish_generation(st, self.project, generation, source))
                    return IndexResult(1, loaded["files"], loaded["files"], loaded["nodes"],
                                       loaded["relationships"], reasons)

                res = self.worker.submit(load).result()
            else:
                def load(st):
                    result = index_repository(st, generation, self.repo_root, progress)
                    result.degraded_reasons.extend(publish_generation(st, self.project, generation, source))
                    return result

                res = self.worker.submit(load).result()
            state = "degraded" if res.degraded_reasons else "indexed"
            write_status(sp, state=state, repo_root=self.repo_root, job_id=job_id,
                         files_total=res.files_total, files_done=res.files_done,
                         nodes_persisted=res.nodes, degraded_reasons=res.degraded_reasons,
                         error=None, source=source, experimental=source == "codegraph",
                         generation=generation, rejected_edges=rejected)
        except Exception as e:                          # noqa: BLE001
            log.exception("index job failed")
            write_status(sp, state="failed", repo_root=self.repo_root, job_id=job_id,
                         files_total=0, files_done=0, nodes_persisted=0,
                         degraded_reasons=[], error=str(e), source=source, experimental=source == "codegraph")

    # ---- ops ----
    def op_ping(self, **_):
        return {"pong": True, "project": self.project, "pid": os.getpid(),
                "index_sources": ["native", "codegraph"]}

    def op_index(self, source: str = "native", **_):
        if source not in ("native", "codegraph"):
            raise ValueError("source must be native|codegraph")
        if self._stop.is_set():
            raise ValueError("daemon is shutting down")
        # Handlers run in the executor pool, so two concurrent "index" requests can
        # both observe is_alive() == False before either starts a thread. Hold the
        # lock across the check AND the thread creation/start so only one index job
        # is ever in flight at a time.
        with self._index_lock:
            if self._index_thread and self._index_thread.is_alive():
                return {"job_id": read_status(self.paths.status_json).get("job_id"),
                        "state": "running"}
            job_id = f"idx-{uuid.uuid4().hex[:8]}"
            self._index_cancel.clear()
            write_status(self.paths.status_json, state="queued", repo_root=self.repo_root,
                         job_id=job_id, files_total=0, files_done=0, nodes_persisted=0,
                         degraded_reasons=[], error=None, source=source, experimental=source == "codegraph")
            self._index_thread = threading.Thread(target=self._index_job, args=(job_id, source), daemon=True)
            self._index_thread.start()
        return {"job_id": job_id, "state": "queued"}

    def op_status(self, **_):
        return read_status(self.paths.status_json)     # never touches chdb — stays responsive

    def op_search(self, **params):
        page = self.worker.submit(
            lambda st: search_mod.search_graph(st, active_project(st, self.project), **params)).result()
        return dataclasses.asdict(page)

    def op_snippet(self, qualified_name: str, **_):
        row = self.worker.submit(lambda st: st.rows(
            f"SELECT file_path, start_line, end_line, properties FROM chgraph.nodes FINAL "
            f"WHERE project = {_q(active_project(st, self.project))} AND qualified_name = {_q(qualified_name)}"
        )).result()
        if not row:
            raise ValueError(f"unknown symbol: {qualified_name}")
        r = row[0]
        import hashlib

        source = read_source(self.repo_root, r["file_path"])
        expected = json.loads(r["properties"]).get("content_hash")
        if expected and hashlib.sha256(source).hexdigest() != expected:
            raise ValueError(f"stale source: {r['file_path']}; reindex before requesting snippets")
        lines = source.decode(errors="replace").splitlines()
        text = "\n".join(lines[r["start_line"] - 1:r["end_line"]])
        return {"qualified_name": qualified_name, "file_path": r["file_path"],
                "start_line": r["start_line"], "end_line": r["end_line"], "text": text}

    def op_trace(self, qualified_name: str, direction: str = "callees", depth: int = 5, **_):
        rows = self.worker.submit(lambda st: traverse_mod.trace_path(
            st, active_project(st, self.project), qualified_name, direction=direction, depth=depth)).result()
        return {"paths": rows}

    def op_explore(self, query: str, max_chars: int = 12000, max_nodes: int = 30, depth: int = 2, **_):
        return self.worker.submit(lambda st: context_mod.explore(
            st, active_project(st, self.project), self.repo_root, query,
            max_chars=max_chars, max_nodes=max_nodes, depth=depth)).result()

    def op_impact(self, qualified_name: str, depth: int = 5, max_nodes: int = 100, **_):
        return self.worker.submit(lambda st: context_mod.impact(
            st, active_project(st, self.project), qualified_name, depth=depth, max_nodes=max_nodes,
            repo_root=self.repo_root)).result()

    def op_schema_info(self, **_):
        def q(st):
            project = active_project(st, self.project)
            labels = [r["label"] for r in st.rows(
                f"SELECT DISTINCT label FROM chgraph.nodes FINAL WHERE project = {_q(project)} ORDER BY label")]
            etypes = [r["type"] for r in st.rows(
                f"SELECT DISTINCT type FROM chgraph.edges FINAL WHERE project = {_q(project)} ORDER BY type")]
            return {"labels": labels, "edge_types": etypes}
        return self.worker.submit(q).result()

    def op_list_projects(self, **_):
        return {"projects": [{"project": self.project, "repo_root": self.repo_root,
                              "state": read_status(self.paths.status_json)["state"]}]}

    def op_delete_project(self, **_):
        # Guard against racing an in-flight index job (consistent with op_index's use
        # of _index_lock): a concurrent delete + index would race the status.json
        # writers and could leave a final status that disagrees with table contents.
        with self._index_lock:
            if self._index_thread and self._index_thread.is_alive():
                raise ValueError("cannot delete while an index job is running")

            def wipe(st):
                for t in ("nodes", "edges", "git_commits", "git_file_changes",
                          "file_evolution", "embeddings"):
                    st.exec(f"TRUNCATE TABLE chgraph.{t}")
                st.delete_rows("chgraph.meta", "key != 'schema_version'")
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
            try:
                req = json.loads(line.decode())
                op = req.get("op", "")
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                writer.write(b'{"ok": false, "error": "malformed request"}\n')
                await writer.drain()
                return
            handler = getattr(self, f"op_{op}", None)
            if op == "shutdown":
                writer.write(b'{"ok": true, "data": {"bye": true}}\n')
                await writer.drain()
                self._stop.set()
                return
            if handler is None:
                resp = {"ok": False, "error": f"unknown op: {op}"}
            elif op in ("ping", "status", "list_projects"):
                # These never touch the SessionWorker (they only read a file or
                # return a small in-memory dict), so call them directly on the
                # event loop instead of queueing behind run_in_executor's shared
                # ThreadPoolExecutor — otherwise a long index job's worker-bound
                # ops (which block in Future.result() for the executor thread's
                # whole lifetime) can starve these "stay responsive mid-index" ops.
                try:
                    data = handler(**req.get("params", {}))
                    resp = {"ok": True, "data": data}
                except Exception as e:                  # noqa: BLE001
                    resp = {"ok": False, "error": str(e)}
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
        try:
            async with server:
                await self._stop.wait()
        finally:
            self._index_cancel.set()
            if self._index_thread:
                self._index_thread.join(timeout=5)
            # A crash here must never leave a stale socket/pidfile behind a held
            # chdb dir lock (chgraph-run-and-operate §2), so both the worker
            # shutdown and the unlink below run unconditionally.
            try:
                self.worker.stop()
            except Exception:                           # noqa: BLE001 — best-effort on shutdown
                log.exception("Daemon.serve: worker.stop() raised during shutdown")
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
    try:
        asyncio.run(d.serve())
    except OSError as e:
        # start_unix_server binds inside serve(), not Daemon.__init__ — a bind
        # failure (address-in-use, or AF_UNIX sockaddr_un's ~104-byte path limit
        # on macOS) would otherwise surface as an opaque traceback.
        log.exception("failed to bind daemon socket %s", paths.socket)
        print(f"cannot bind daemon socket {paths.socket}: {e} "
              f"(path too long? set a shorter CHGRAPH_DATA_DIR)", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    run_daemon(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())
