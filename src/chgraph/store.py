"""Store: sole owner of the chdb Session in a process (INV-1)."""
import json
from pathlib import Path

import chdb.session as chs

from chgraph import schema


class SchemaVersionError(RuntimeError):
    pass


class Store:
    def __init__(self, sess, *, durable: bool = False):
        self._sess = sess
        self._durable = durable

    @classmethod
    def open(cls, chdb_dir: str | Path, *, durable_uri: str | None = None,
             project: str | None = None) -> "Store":
        if durable_uri:
            if not project:
                raise ValueError("durable storage requires a project id")
            from chdb import durable as cd

            sess = cd.Namespace(durable_uri, owner="chgraph-daemon", db="chgraph").open(project)
        else:
            sess = chs.Session(str(chdb_dir))
        store = cls(sess, durable=bool(durable_uri))
        try:
            schema.create_all(store, create_database=not store._durable)
            rows = store.rows("SELECT value FROM chgraph.meta FINAL WHERE key = 'schema_version'")
            if not rows:
                store.exec(
                    f"INSERT INTO chgraph.meta VALUES ('schema_version', '{schema.SCHEMA_VERSION}', 1)"
                )
                store.checkpoint()
            elif int(rows[0]["value"]) != schema.SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"data dir has schema_version={rows[0]['value']}, "
                    f"this build understands {schema.SCHEMA_VERSION} (INV-6: refusing to open)"
                )
        except Exception:
            sess.close()
            raise
        return store

    def exec(self, sql: str) -> None:
        if self._durable:
            self._sess.execute(sql)
            self._sess.flush()
        else:
            self._sess.query(sql)

    def checkpoint(self) -> None:
        if self._durable:
            self._sess.checkpoint()

    def rows(self, sql: str) -> list[dict]:
        out = self._sess.query(sql, "JSONEachRow").bytes().decode()
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def raw(self, sql: str, fmt: str = "CSV") -> str:
        return self._sess.query(sql, fmt).bytes().decode()

    def close(self) -> None:
        self._sess.close()
