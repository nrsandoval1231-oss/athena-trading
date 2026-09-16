"""Append-only round ledger plus per-agent / per-regime accuracy hooks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from astra_desk.types import Regime

GENESIS_HASH = "0" * 64


def canonical_hash(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    round_id: str
    recorded_at: str
    payload: Mapping[str, Any]
    prev_hash: str
    entry_hash: str


class AppendOnlyLedger:
    """Immutable log: entries can only be appended. No rewrite API."""

    def __init__(self) -> None:
        self._entries: tuple[LedgerEntry, ...] = ()

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return self._entries

    @property
    def head_hash(self) -> str:
        if not self._entries:
            return GENESIS_HASH
        return self._entries[-1].entry_hash

    def append(self, round_id: str, payload: Mapping[str, Any]) -> LedgerEntry:
        prev = self.head_hash
        recorded_at = datetime.now(timezone.utc).isoformat()
        body = {
            "seq": len(self._entries),
            "round_id": round_id,
            "recorded_at": recorded_at,
            "payload": payload,
            "prev_hash": prev,
        }
        entry = LedgerEntry(
            seq=body["seq"],
            round_id=round_id,
            recorded_at=recorded_at,
            payload=dict(payload),
            prev_hash=prev,
            entry_hash=canonical_hash(body),
        )
        self._entries = self._entries + (entry,)
        return entry

    @classmethod
    def from_entries(cls, entries: tuple[LedgerEntry, ...]) -> "AppendOnlyLedger":
        """Load a verified chain. No rewrite of existing entries."""
        ledger = cls()
        ledger._entries = tuple(entries)
        ok, reason = ledger.verify()
        if not ok:
            from astra_desk.errors import LedgerStoreError

            raise LedgerStoreError(f"ledger chain failed verification: {reason}")
        return ledger

    def verify(self) -> tuple[bool, str]:
        """Recompute the append-only hash chain. Empty ledgers are valid."""
        prev = GENESIS_HASH
        for index, entry in enumerate(self._entries):
            if entry.seq != index:
                return False, "seq_mismatch"
            if entry.prev_hash != prev:
                return False, "prev_hash_mismatch"
            body = {
                "seq": entry.seq,
                "round_id": entry.round_id,
                "recorded_at": entry.recorded_at,
                "payload": entry.payload,
                "prev_hash": entry.prev_hash,
            }
            if canonical_hash(body) != entry.entry_hash:
                return False, "entry_hash_mismatch"
            prev = entry.entry_hash
        return True, "ledger_verified"


@dataclass(frozen=True)
class SetupOutcome:
    """One setup's per-agent correctness. Ledger/calibration source of truth."""

    setup_id: str
    regime: str
    correct_by_agent: Mapping[str, bool]


def outcomes_from_ledger(ledger: AppendOnlyLedger) -> tuple[SetupOutcome, ...]:
    """Rebuild setup outcomes from the append-only ledger. Does not invent accuracy."""
    found: list[SetupOutcome] = []
    for entry in ledger.entries:
        payload = entry.payload
        raw = payload.get("outcomes")
        if not isinstance(raw, Mapping) or not raw:
            continue
        regime = str(payload.get("regime") or "")
        setup_id = str(payload.get("setup_id") or entry.round_id)
        correct = {str(agent_id): bool(flag) for agent_id, flag in raw.items()}
        found.append(SetupOutcome(setup_id=setup_id, regime=regime, correct_by_agent=correct))
    return tuple(found)


class AccuracyTracker:
    """Hits / n per agent and per regime, plus ordered setup outcomes for rolling windows."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], int] = {}
        self._n: dict[tuple[str, str], int] = {}
        self._history: list[SetupOutcome] = []

    @property
    def history(self) -> tuple[SetupOutcome, ...]:
        return tuple(self._history)

    def record(self, agent_id: str, regime: Regime, correct: bool) -> None:
        key = (agent_id, regime.value)
        self._n[key] = self._n.get(key, 0) + 1
        if correct:
            self._hits[key] = self._hits.get(key, 0) + 1

    def record_setup(
        self,
        setup_id: str,
        regime: Regime,
        correct_by_agent: Mapping[str, bool],
    ) -> None:
        outcome = SetupOutcome(
            setup_id=setup_id,
            regime=regime.value,
            correct_by_agent={str(k): bool(v) for k, v in correct_by_agent.items()},
        )
        self._history.append(outcome)
        for agent_id, correct in outcome.correct_by_agent.items():
            self.record(agent_id, regime, correct)

    def load_from_ledger(self, ledger: AppendOnlyLedger) -> None:
        """Replace in-memory history with ledger outcomes. Ledger is source of truth."""
        self._hits = {}
        self._n = {}
        self._history = []
        for outcome in outcomes_from_ledger(ledger):
            try:
                regime = Regime(outcome.regime)
            except ValueError:
                continue
            self.record_setup(outcome.setup_id, regime, outcome.correct_by_agent)

    def rolling(self, window: int) -> tuple[SetupOutcome, ...]:
        if window < 1:
            return ()
        return tuple(self._history[-window:])

    def agent_accuracy(self) -> dict[str, float]:
        totals: dict[str, list[int]] = {}
        for (agent_id, _regime), n in self._n.items():
            hits = self._hits.get((agent_id, _regime), 0)
            bucket = totals.setdefault(agent_id, [0, 0])
            bucket[0] += hits
            bucket[1] += n
        return {
            agent_id: (hits / n if n else 0.0)
            for agent_id, (hits, n) in totals.items()
        }

    def regime_stats(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for (agent_id, regime), n in self._n.items():
            hits = self._hits.get((agent_id, regime), 0)
            slot = out.setdefault(regime, {})
            slot[agent_id] = {"hits": hits, "n": n, "accuracy": hits / n if n else 0.0}
        return out

    def rolling_agent_regime_accuracy(
        self, agent_id: str, regime: Regime
    ) -> float | None:
        n = self._n.get((agent_id, regime.value), 0)
        if n == 0:
            return None
        return self._hits.get((agent_id, regime.value), 0) / n
