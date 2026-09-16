"""Local append-only ledger file store. No cloud DB, no secrets.

Chain file is JSONL and never rewritten. Snapshot.json holds books /
kill-switch / working weights so a later process can resume safely.
Corrupt chains fail closed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from astra_desk.accounting import CapitalBooks
from astra_desk.errors import LedgerStoreError
from astra_desk.ledger import GENESIS_HASH, AppendOnlyLedger, LedgerEntry
from astra_desk.types import Position

STORE_SCHEMA = "astra-desk/ledger-store-v1"
CHAIN_NAME = "chain.jsonl"
SNAPSHOT_NAME = "snapshot.json"
ENV_DATA_DIR = "ASTRA_DESK_DATA_DIR"
DEFAULT_DATA_DIR = Path("data") / "ledger"


def default_store_path() -> Path:
    env = os.environ.get(ENV_DATA_DIR)
    if env:
        return Path(env)
    return Path.cwd() / DEFAULT_DATA_DIR


def resolve_store_path(path: str | Path | None = None) -> Path:
    if path is None:
        return default_store_path()
    resolved = Path(path)
    if resolved.suffix:
        raise LedgerStoreError("ledger store path must be a directory, not a file")
    return resolved


def entry_to_dict(entry: LedgerEntry) -> dict[str, Any]:
    return {
        "seq": entry.seq,
        "round_id": entry.round_id,
        "recorded_at": entry.recorded_at,
        "payload": dict(entry.payload),
        "prev_hash": entry.prev_hash,
        "entry_hash": entry.entry_hash,
    }


def entry_from_dict(raw: Mapping[str, Any]) -> LedgerEntry:
    try:
        return LedgerEntry(
            seq=int(raw["seq"]),
            round_id=str(raw["round_id"]),
            recorded_at=str(raw["recorded_at"]),
            payload=dict(raw["payload"]) if isinstance(raw["payload"], Mapping) else {},
            prev_hash=str(raw["prev_hash"]),
            entry_hash=str(raw["entry_hash"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LedgerStoreError(f"invalid ledger entry: {exc}") from exc


class PersistentLedger(AppendOnlyLedger):
    """Append-only ledger that mirrors each new entry to disk."""

    def __init__(self, store: "LedgerStore", entries: tuple[LedgerEntry, ...] = ()) -> None:
        super().__init__()
        self._store = store
        self._entries = entries
        self._desk: Any = None

    def bind_desk(self, desk: Any) -> None:
        self._desk = desk

    def append(self, round_id: str, payload: Mapping[str, Any]) -> LedgerEntry:
        entry = super().append(round_id, payload)
        self._store.append_entry(entry)
        if self._desk is not None:
            self._store.write_snapshot(self._desk)
        return entry


class LedgerStore:
    """Directory store: chain.jsonl (append-only) + snapshot.json (resume state)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.chain_path = path / CHAIN_NAME
        self.snapshot_path = path / SNAPSHOT_NAME

    @classmethod
    def open(cls, path: str | Path | None, *, new: bool = False) -> "LedgerStore":
        store_path = resolve_store_path(path)
        store = cls(store_path)
        if new:
            store._create()
        else:
            store._require_existing()
        return store

    def _create(self) -> None:
        if self.chain_path.is_file() and self.chain_path.stat().st_size > 0:
            raise LedgerStoreError(
                f"ledger store already exists at {self.path}; omit --new-store to resume"
            )
        self.path.mkdir(parents=True, exist_ok=True)
        if not self.chain_path.exists():
            self.chain_path.touch()
        self._write_json(
            self.snapshot_path,
            {
                "schema": STORE_SCHEMA,
                "live_trading_authority": False,
                "head_hash": GENESIS_HASH,
                "entry_count": 0,
                "round_seq": 0,
                "books": None,
                "kill_switch": None,
                "conviction_weights": None,
                "disabled_seats": {},
            },
        )

    def _require_existing(self) -> None:
        if not self.chain_path.is_file():
            raise LedgerStoreError(
                f"ledger store not found at {self.path}; pass --new-store to create it"
            )

    def load_entries(self) -> tuple[LedgerEntry, ...]:
        try:
            raw = self.chain_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LedgerStoreError(f"failed to read ledger chain {self.chain_path}: {exc}") from exc
        entries: list[LedgerEntry] = []
        for line_no, line in enumerate(raw.splitlines(), start=1):
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise LedgerStoreError(
                    f"corrupt ledger chain at {self.chain_path}:{line_no}"
                ) from exc
            if not isinstance(parsed, Mapping):
                raise LedgerStoreError(f"corrupt ledger chain at {self.chain_path}:{line_no}")
            entries.append(entry_from_dict(parsed))
        return AppendOnlyLedger.from_entries(tuple(entries)).entries

    def append_entry(self, entry: LedgerEntry) -> None:
        payload = json.dumps(entry_to_dict(entry), sort_keys=True, separators=(",", ":"))
        self.path.mkdir(parents=True, exist_ok=True)
        with self.chain_path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def write_snapshot(self, desk: Any) -> None:
        books: CapitalBooks = desk.books
        snap = {
            "schema": STORE_SCHEMA,
            "live_trading_authority": False,
            "head_hash": desk.ledger.head_hash,
            "entry_count": len(desk.ledger),
            "round_seq": int(desk._round_seq),
            "books": {
                "currency": books.currency,
                "starting_equity": books.starting_equity,
                "realized_pnl": books.realized_pnl,
                "unrealized_pnl": books.unrealized_pnl,
                "peak_equity": books.peak_equity,
                "max_drawdown_pct": books.max_drawdown_pct,
                "day_start_equity": books.day_start_equity,
                "closed_pnls": list(books._closed_pnls),
                "positions": [
                    {
                        "symbol": pos.symbol,
                        "notional_usd": pos.notional_usd,
                        "correlation_group": pos.correlation_group,
                        "side": pos.side,
                    }
                    for pos in books.positions
                ],
            },
            "kill_switch": {
                "halted": desk.kill_switch.halted,
                "reason": desk.kill_switch.state().reason,
                "armed": desk.kill_switch._armed,
            },
            "conviction_weights": dict(desk.conviction_weights),
            "disabled_seats": dict(desk.disabled_seats),
        }
        self._write_json(self.snapshot_path, snap)

    def attach(self, desk: Any) -> PersistentLedger:
        entries = self.load_entries()
        ledger = PersistentLedger(self, entries)
        ok, reason = ledger.verify()
        if not ok:
            raise LedgerStoreError(f"ledger chain failed verification: {reason}")
        desk.ledger = ledger
        ledger.bind_desk(desk)
        snapshot = self._read_snapshot()
        self._restore_desk(desk, snapshot)
        desk.accuracy.load_from_ledger(desk.ledger)
        ledger.bind_desk(desk)
        return ledger

    def _read_snapshot(self) -> Mapping[str, Any]:
        if not self.snapshot_path.is_file():
            raise LedgerStoreError(f"ledger snapshot missing at {self.snapshot_path}")
        try:
            data = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LedgerStoreError(f"failed to read ledger snapshot: {exc}") from exc
        if not isinstance(data, Mapping):
            raise LedgerStoreError("ledger snapshot must be a JSON object")
        if data.get("schema") != STORE_SCHEMA:
            raise LedgerStoreError(f"unsupported ledger snapshot schema: {data.get('schema')!r}")
        if data.get("live_trading_authority") is True:
            from astra_desk.errors import LiveTradingDisabled

            raise LiveTradingDisabled("ledger snapshot claims live_trading_authority")
        return data

    def _restore_desk(self, desk: Any, snapshot: Mapping[str, Any]) -> None:
        head = str(snapshot.get("head_hash") or "")
        if head != desk.ledger.head_hash:
            raise LedgerStoreError(
                "ledger snapshot head_hash does not match chain; refuse to resume"
            )
        count = snapshot.get("entry_count")
        if count is not None and int(count) != len(desk.ledger):
            raise LedgerStoreError("ledger snapshot entry_count does not match chain")
        desk._round_seq = int(snapshot.get("round_seq") or 0)
        books_raw = snapshot.get("books")
        if isinstance(books_raw, Mapping):
            positions = tuple(
                Position(
                    symbol=str(item["symbol"]),
                    notional_usd=float(item["notional_usd"]),
                    correlation_group=str(item["correlation_group"]),
                    side=str(item.get("side") or "long"),
                )
                for item in (books_raw.get("positions") or [])
                if isinstance(item, Mapping)
            )
            desk.books.restore_snapshot(
                realized_pnl=float(books_raw.get("realized_pnl") or 0.0),
                unrealized_pnl=float(books_raw.get("unrealized_pnl") or 0.0),
                peak_equity=float(
                    books_raw.get("peak_equity") or desk.books.starting_equity
                ),
                max_drawdown_pct=float(books_raw.get("max_drawdown_pct") or 0.0),
                day_start_equity=float(
                    books_raw.get("day_start_equity") or desk.books.starting_equity
                ),
                closed_pnls=list(books_raw.get("closed_pnls") or []),
                positions=positions,
            )
        ks_raw = snapshot.get("kill_switch")
        if isinstance(ks_raw, Mapping):
            desk.kill_switch.restore_snapshot(
                halted=bool(ks_raw.get("halted")),
                reason=ks_raw.get("reason"),
                armed=bool(ks_raw.get("armed", True)),
            )
        weights = snapshot.get("conviction_weights")
        if isinstance(weights, Mapping) and weights:
            desk.conviction_weights = {str(k): float(v) for k, v in weights.items()}
        seats = snapshot.get("disabled_seats")
        if isinstance(seats, Mapping):
            desk.disabled_seats = {str(k): str(v) for k, v in seats.items()}

    def _write_json(self, path: Path, payload: Mapping[str, Any]) -> None:
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)

    def load_verified_chain(self) -> tuple[AppendOnlyLedger, Mapping[str, Any]]:
        """Read-only load. Verifies the hash chain and snapshot head. Does not write."""
        entries = self.load_entries()
        ledger = AppendOnlyLedger.from_entries(entries)
        snapshot = self._read_snapshot()
        head = str(snapshot.get("head_hash") or "")
        if head != ledger.head_hash:
            raise LedgerStoreError(
                "ledger snapshot head_hash does not match chain; refuse to report"
            )
        count = snapshot.get("entry_count")
        if count is not None and int(count) != len(ledger):
            raise LedgerStoreError("ledger snapshot entry_count does not match chain")
        return ledger, snapshot


def attach_store(desk: Any, path: str | Path | None, *, new: bool = False) -> LedgerStore:
    store = LedgerStore.open(path, new=new)
    store.attach(desk)
    store.write_snapshot(desk)
    return store
