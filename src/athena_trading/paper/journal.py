"""Single-writer, durable paper-session events. Recover by replaying verified rows."""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from ..athena_adapter.contracts import canonical_hash, canonical_json


class PaperJournal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = path.with_suffix(path.suffix + ".lock").open("a")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            raise ValueError("another paper runner holds this journal") from None
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events "
            "(seq INTEGER PRIMARY KEY, payload TEXT NOT NULL, previous TEXT NOT NULL, "
            "digest TEXT NOT NULL)"
        )
        self.db.commit()
        self.events: list[dict[str, Any]] = []
        self.head = "0" * 64
        try:
            for seq, payload, previous, digest in self.db.execute(
                "SELECT seq, payload, previous, digest FROM events ORDER BY seq"
            ):
                event = json.loads(payload)
                expected = canonical_hash({"seq": seq, "event": event, "previous": previous})
                if seq != len(self.events) + 1 or previous != self.head or digest != expected:
                    raise ValueError("paper journal integrity check failed")
                self.events.append(event)
                self.head = digest
        except Exception:
            self.close()
            raise

    def append(self, kind: str, **data: Any) -> None:
        event = json.loads(canonical_json({"kind": kind, **data}))
        seq = len(self.events) + 1
        digest = canonical_hash({"seq": seq, "event": event, "previous": self.head})
        with self.db:
            self.db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?)",
                (seq, canonical_json(event), self.head, digest),
            )
        self.events.append(event)
        self.head = digest

    def last(self, kind: str) -> dict[str, Any] | None:
        return next((event for event in reversed(self.events) if event["kind"] == kind), None)

    def close(self) -> None:
        self.db.close()
        self._lock.close()
