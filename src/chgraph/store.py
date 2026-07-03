"""Store: sole owner of the chdb Session in a process (INV-1)."""
import json
from pathlib import Path

import chdb.session as chs

from chgraph import schema


class SchemaVersionError(RuntimeError):
    pass


class Store:
    def __init__(self, sess):
        self._sess = sess

    @classmethod
    def open(cls, chdb_dir: str | Path) -> "Store":
        sess = chs.Session(str(chdb_dir))
        store = cls(sess)
        try:
            schema.create_all(sess)
            rows = store.rows("SELECT value FROM chgraph.meta FINAL WHERE key = 'schema_version'")
            if not rows:
                store.exec(
                    f"INSERT INTO chgraph.meta VALUES ('schema_version', '{schema.SCHEMA_VERSION}', 1)"
                )
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
        self._sess.query(sql)

    def rows(self, sql: str) -> list[dict]:
        out = self._sess.query(sql, "JSONEachRow").bytes().decode()
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def raw(self, sql: str, fmt: str = "CSV") -> str:
        return self._sess.query(sql, fmt).bytes().decode()

    def close(self) -> None:
        self._sess.close()
