"""Paper journal durability under a concurrent read-only peer."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from athena_trading.paper.journal import PaperJournal


def test_writer_waits_out_a_peer_read_longer_than_the_old_five_seconds(tmp_path: Path):
    """Earnings logged 'database is locked' while the paper runner read its book.

    The peer read holds a shared lock. The old default busy timeout was 5 seconds.
    A slow disk can hold that lock longer. The writer waits instead of raising.
    """
    path = tmp_path / "book.sqlite"
    journal = PaperJournal(path)
    journal.append("binding", binding={"account_id": "paper"})
    timeout_ms = journal.db.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout_ms >= 30_000
    uri = path.resolve().as_uri() + "?mode=ro"
    release = threading.Event()
    started = threading.Event()

    def hold() -> None:
        db = sqlite3.connect(uri, uri=True, timeout=30.0)
        try:
            db.execute("BEGIN")
            db.execute("SELECT COUNT(*) FROM events").fetchone()
            started.set()
            release.wait(20)
        finally:
            db.close()

    thread = threading.Thread(target=hold)
    thread.start()
    assert started.wait(5)
    timer = threading.Timer(6.0, release.set)
    timer.start()
    try:
        journal.append("still_writing", n=1)
    finally:
        release.set()
        timer.cancel()
        thread.join(10)
        journal.close()
    reopened = PaperJournal(path)
    try:
        assert reopened.last("still_writing")["n"] == 1
    finally:
        reopened.close()
