"""One active live-data paper pilot using ORB input, an LLM Actor, and Jev.

One share, one entry per session, one dedicated paper account. This is the first
intraday execution slice, not the complete dual-strategy or learning system.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from ..athena_adapter.contracts import canonical_hash, canonical_json
from ..athena_adapter.jev import JevAdjudicator, ModelError, request_json
from ..orb_m.constitution import Constitution
from ..orb_m.entry import build_risk_frame, detect_breakout, freeze_opening_range
from ..orb_m.market import Bar, average_true_range
from .alpaca import AlpacaPaperBroker, PAPER_API_BASE
from .journal import PaperJournal

NY = ZoneInfo("America/New_York")
TERMINAL = {"filled", "canceled", "expired", "rejected", "replaced"}


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return result.astimezone(UTC)


def number(value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a market/account number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError("invalid market/account number")
    return result


@dataclass(frozen=True)
class PilotPolicy:
    symbol: str
    feed: str = "iex"
    actor_model: str = "qwen2.5:7b"
    max_notional: float = 1000.0
    max_stop_risk: float = 10.0
    max_daily_loss_fraction: float = 0.005
    support_min: float = 0.8
    confidence_min: float = 0.6
    max_adjudications: int = 20
    close_buffer_minutes: int = 15
    max_bar_age_seconds: int = 90
    version: str = "athena-active-paper-pilot-v1"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", self.symbol):
            raise ValueError("an explicit uppercase equity symbol is required")
        if self.feed not in {"iex", "sip"} or not self.actor_model.strip():
            raise ValueError("invalid feed or Actor model")
        if not 0 < number(self.max_notional) <= 1000 or not 0 < number(self.max_stop_risk) <= 10:
            raise ValueError("pilot caps are $1,000 notional and $10 initial stop risk")
        if not 0 < number(self.max_daily_loss_fraction) <= 0.005:
            raise ValueError("daily account loss cap must be at most 0.5%")
        if not 0 < number(self.support_min) <= 1 or not 0 < number(self.confidence_min) <= 1:
            raise ValueError("invalid Jev thresholds")
        if type(self.max_adjudications) is not int or not 1 <= self.max_adjudications <= 20:
            raise ValueError("at most 20 adjudications per session")
        if self.close_buffer_minutes < 15 or not 1 <= self.max_bar_age_seconds <= 90:
            raise ValueError("invalid session/freshness policy")


class LivePaperAPI(AlpacaPaperBroker):
    """Extend the existing paper-only adapter with data and position lifecycle calls."""

    def __init__(self, api_key: str, api_secret: str, *, transport=request_json) -> None:
        super().__init__(api_key, api_secret)
        self.http = transport

    def call(self, method: str, path: str, payload: Any = None) -> Any:
        return self.http(method, PAPER_API_BASE + path, self._headers(), payload, timeout=5)

    def bars(self, symbol: str, start: datetime, end: datetime, timeframe: str, feed: str):
        params = {
            "symbols": symbol, "start": start.isoformat(), "end": end.isoformat(),
            "timeframe": timeframe, "feed": feed, "adjustment": "raw", "limit": 10000,
            "sort": "asc",
        }
        result, tokens = [], set()
        for _ in range(20):
            raw = self.http(
                "GET", "https://data.alpaca.markets/v2/stocks/bars?" + urlencode(params),
                self._headers(), timeout=5,
            )
            result.extend(raw["bars"].get(symbol, []))
            token = raw.get("next_page_token")
            if not token:
                return result
            if token in tokens:
                break
            tokens.add(token)
            params["page_token"] = token
        raise ValueError("incomplete market-data pagination")

    def snapshot(self) -> dict[str, Any]:
        return {
            "clock": self.call("GET", "/v2/clock"),
            "account": self.call("GET", "/v2/account"),
            "positions": self.call("GET", "/v2/positions"),
            "orders": self.call("GET", "/v2/orders?status=open&nested=false&limit=500"),
        }


class OllamaActor:
    """Reuse the repo's local Ollama choice; pin its digest for this session."""

    def __init__(self, model: str = "qwen2.5:7b", *, transport=request_json) -> None:
        self.model, self.http = model, transport
        self.digest = self._digest()

    def _digest(self) -> str:
        tags = self.http("GET", "http://127.0.0.1:11434/api/tags", {}, timeout=5)
        matches = [item for item in tags["models"] if item.get("name") == self.model]
        if len(matches) != 1 or not re.fullmatch(r"[a-f0-9]{64}", matches[0].get("digest", "")):
            raise ModelError("the configured Ollama model must already be installed")
        return matches[0]["digest"]

    def propose(self, packet: dict[str, Any]) -> dict[str, Any]:
        if self._digest() != self.digest:
            raise ModelError("Actor model digest changed")
        response = self.http(
            "POST", "http://127.0.0.1:11434/api/chat", {"Content-Type": "application/json"},
            {
                "model": self.model, "stream": False, "format": "json",
                "options": {"temperature": 0, "num_predict": 250},
                "messages": [
                    {"role": "system", "content":
                     "You are Athena's intraday Decision Actor. Given frozen observations, "
                     "propose the candidate direction or NO_TRADE. Return exactly a JSON object "
                     "with action, rationale, invalidation. Use only supplied evidence. "
                     "Describe a testable thesis, not guaranteed returns. Do not invent news, "
                     "lessons, numbers, or account facts. Sizing and stops belong to risk code."},
                    {"role": "user", "content": canonical_json(packet)},
                ],
            }, timeout=25,
        )
        if response.get("model") != self.model or response.get("done") is not True:
            raise ModelError("Actor response identity/completion mismatch")
        try:
            proposal = json.loads(response["message"]["content"])
        except (ValueError, KeyError, TypeError):
            raise ModelError("Actor did not return a JSON proposal") from None
        if not isinstance(proposal, dict) or set(proposal) != {"action", "rationale", "invalidation"}:
            raise ModelError("Actor proposal schema mismatch")
        if proposal["action"] not in {packet["candidate_direction"], "NO_TRADE"}:
            raise ModelError("Actor changed the candidate direction")
        if any(not isinstance(proposal[key], str) or not 1 <= len(proposal[key]) <= 1500
               for key in ("rationale", "invalidation")):
            raise ModelError("Actor proposal text is missing or oversized")
        return {**proposal, "actor_model": self.model, "actor_digest": self.digest}


def signal_packet(
    raw_bars: list[dict[str, Any]], daily: list[dict[str, Any]],
    *, now: datetime, session_open: datetime, policy: PilotPolicy,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Only a fresh completed minute may trigger; no historical catch-up orders."""
    bars, seen = [], set()
    for raw in raw_bars:
        event_at = timestamp(raw["t"])
        if event_at in seen:
            raise ValueError("duplicate market bar")
        seen.add(event_at)
        if event_at < session_open or event_at + timedelta(minutes=1) > now:
            continue
        minute = int((event_at - session_open).total_seconds() // 60)
        if event_at.second or event_at.microsecond:
            raise ValueError("unaligned market bar")
        bars.append(Bar(event_at, minute, *(number(raw[k], positive=True)
                                         for k in ("o", "h", "l", "c")),
                        number(raw["v"])))
    bars.sort(key=lambda b: b.event_at)
    if not bars or bars[-1].minute < 15:
        return None
    if (now - (bars[-1].event_at + timedelta(minutes=1))).total_seconds() > policy.max_bar_age_seconds:
        raise ValueError("latest completed bar is stale")
    constitution = Constitution()
    opening = freeze_opening_range(bars, constitution)
    signal = detect_breakout(bars[-1:], opening, constitution)
    if signal is None:
        return None
    prior = sorted((raw for raw in daily if timestamp(raw["t"]).astimezone(NY).date()
                    < session_open.astimezone(NY).date()), key=lambda raw: raw["t"])
    if len({raw["t"] for raw in prior}) != len(prior):
        raise ValueError("duplicate daily bars")
    atr = average_true_range([(number(d["h"], positive=True), number(d["l"], positive=True),
                               number(d["c"], positive=True)) for d in prior])
    frame = build_risk_frame(signal, signal.signal_close, atr, constitution)
    price = signal.signal_close
    sign = 1 if signal.direction == "LONG" else -1
    stop = round(frame.stop_price, 2)
    risk = abs(price - stop)
    if price < constitution.price_min or atr < constitution.atr14_min or risk < 0.02:
        return None
    packet = {
        "strategy": "PREDICTIVE_INTRADAY", "signal_component": "opening_range_breakout",
        "symbol": policy.symbol, "as_of": now.isoformat(),
        "signal_at": bars[-1].event_at.isoformat(), "feed": policy.feed,
        "candidate_direction": signal.direction,
        "context": {"version": "empty-v1", "validated_lessons": [], "learning_active": False},
        "observations": {
            "breakout": "closed above the opening range" if sign == 1 else
                        "closed below the opening range",
            "bar_direction": "up" if bars[-1].close > bars[-1].open else "down_or_flat",
            "opening_range_high": opening.high, "opening_range_low": opening.low,
            "latest_completed_close": price, "atr14": atr,
            "news": "unavailable", "sector_context": "unavailable",
            "forecast_probability": "unvalidated", "risk_stop": stop,
        },
        "input_hash": canonical_hash({"bars": bars, "daily": prior}),
    }
    order = {
        "symbol": policy.symbol, "qty": "1", "side": "buy" if sign == 1 else "sell",
        "type": "limit", "limit_price": f"{price:.2f}", "time_in_force": "day",
        "order_class": "bracket", "extended_hours": False,
        "stop_loss": {"stop_price": f"{stop:.2f}"},
        "take_profit": {"limit_price": f"{price + sign * 2 * risk:.2f}"},
    }
    return packet, order


class ActivePaperPilot:
    def __init__(
        self, broker: LivePaperAPI, actor: OllamaActor | None, jev: JevAdjudicator | None,
        journal: PaperJournal, policy: PilotPolicy,
    ) -> None:
        self.broker, self.actor, self.jev = broker, actor, jev
        self.journal, self.policy = journal, policy
        self.daily = None
        self._flat_observations = 0

    def halt(self, reason: str) -> None:
        if not self.journal.last("halt"):
            self.journal.append("halt", reason=reason, at=datetime.now(UTC))

    def _bind(self, snapshot: dict[str, Any]) -> None:
        binding = {"account_id": snapshot["account"]["id"], "policy": asdict(self.policy)}
        previous = self.journal.last("binding")
        if previous:
            if previous["binding"] != binding:
                raise ValueError("journal belongs to a different account or policy")
        else:
            if snapshot["positions"] or snapshot["orders"]:
                raise ValueError("pilot requires a dedicated, initially flat paper account")
            self.journal.append("binding", binding=binding)

    def _entry(self):
        return self.journal.last("entry_intent")

    def _track_entry(self):
        entry = self._entry()
        if not entry:
            return None
        parent = self.broker.call(
            "GET", "/v2/orders:by_client_order_id?" +
            urlencode({"client_order_id": entry["order"]["client_order_id"]}),
        )
        for key in ("client_order_id", "symbol", "side"):
            if parent.get(key) != entry["order"][key]:
                raise ValueError("recovered order does not match the reserved entry")
        if number(parent["qty"]) != 1 or not 0 <= number(parent["filled_qty"]) <= 1:
            raise ValueError("recovered entry quantity mismatch")
        return parent

    def flatten_step(self, snapshot: dict[str, Any]) -> bool:
        """Halt first; cancel entry/exit reservations, then close confirmed exposure."""
        self.halt("FLATTEN_REQUESTED")
        if not self._entry():
            return not snapshot["positions"] and not snapshot["orders"]
        symbol = self.policy.symbol
        if any(item["symbol"] != symbol for item in snapshot["positions"] + snapshot["orders"]):
            raise ValueError("unowned exposure is present; refusing account-wide cleanup")
        close_events = [e for e in self.journal.events if e["kind"] == "close_ack"]
        close_ids = {e["order"]["id"] for e in close_events}
        working = snapshot["orders"]
        # After a lost close response, reconcile its broker-created order instead of duplicating it.
        pending_close = self.journal.last("close_intent")
        if pending_close and not any(e["attempt"] == pending_close["attempt"] for e in close_events):
            candidates = [o for o in working if o["symbol"] == symbol
                          and o.get("type") == "market" and o.get("order_class", "simple") == "simple"]
            if len(candidates) == 1:
                self.journal.append("close_ack", order=candidates[0], attempt=pending_close["attempt"])
                close_ids.add(candidates[0]["id"])
        cancelable = [o for o in working if o["id"] not in close_ids]
        for order in cancelable:
            self.broker.call("DELETE", "/v2/orders/" + order["id"])
            self.journal.append("cancel_requested", order_id=order["id"])
        if cancelable or any(o["id"] in close_ids for o in working):
            self._flat_observations = 0
            return False  # cancellation/close acknowledgement is not a fill
        if snapshot["positions"]:
            self._flat_observations = 0
            attempts = sum(e["kind"] == "close_intent" for e in self.journal.events)
            if attempts >= 3:
                raise ValueError("close attempts exhausted; exposure remains unresolved")
            self.journal.append("close_intent", attempt=attempts + 1,
                                positions=snapshot["positions"])
            order = self.broker.call("DELETE", "/v2/positions/" + symbol)
            self.journal.append("close_ack", attempt=attempts + 1, order=order)
            return False
        # A possibly accepted entry must be resolved before declaring the account flat.
        parent = self._track_entry()
        if parent["status"] not in TERMINAL:
            self.broker.call("DELETE", "/v2/orders/" + parent["id"])
            return False
        self._flat_observations += 1
        if self._flat_observations < 2:
            return False
        history = self.broker.call("GET", "/v2/orders?" + urlencode({
            "status": "all", "after": (timestamp(self._entry()["at"]) - timedelta(seconds=5)).isoformat(),
            "limit": 500, "direction": "asc", "nested": "false",
        }))
        if not isinstance(history, list) or len(history) >= 500:
            raise ValueError("terminal broker order history is incomplete")
        self.journal.append("flat_confirmed", parent=parent, snapshot=snapshot, order_history=history)
        return True

    def tick(self) -> str:
        snapshot = self.broker.snapshot()
        self._bind(snapshot)
        now = timestamp(snapshot["clock"]["timestamp"])
        day = now.astimezone(NY).date().isoformat()
        self.journal.append("broker_observation", at=now, snapshot=snapshot)
        if self.journal.last("flat_confirmed"):
            if snapshot["positions"] or snapshot["orders"]:
                self.halt("EXPOSURE_AFTER_FLAT")
            else:
                return "COMPLETE"
        if self.journal.last("halt"):
            return "COMPLETE" if self.flatten_step(snapshot) else "FLATTENING"
        clock, account = snapshot["clock"], snapshot["account"]
        close_at = timestamp(clock["next_close"])
        if not clock["is_open"] or now >= close_at - timedelta(minutes=self.policy.close_buffer_minutes):
            if self._entry():
                self.halt("SESSION_CLOSE")
                return "COMPLETE" if self.flatten_step(snapshot) else "FLATTENING"
            return "MARKET_CLOSED" if not clock["is_open"] else "COMPLETE"
        equity = number(account["equity"], positive=True)
        last_equity = number(account["last_equity"], positive=True)
        if equity <= last_equity * (1 - self.policy.max_daily_loss_fraction):
            self.halt("DAILY_LOSS_LIMIT")
            return "COMPLETE" if self.flatten_step(snapshot) else "FLATTENING"
        entry = self._entry()
        if entry:
            if entry["session"] != day:
                self.halt("PRIOR_SESSION_RECOVERY")
                return "COMPLETE" if self.flatten_step(snapshot) else "FLATTENING"
            parent = self._track_entry()
            self.journal.append("entry_observation", order=parent)
            qty = number(parent["filled_qty"])
            if parent["status"] == "partially_filled" or (qty > 0 and parent["status"] != "filled"):
                self.halt("PARTIAL_ENTRY")
            elif parent["status"] in {"canceled", "expired", "rejected", "replaced"}:
                self.halt("ENTRY_TERMINATED")
            elif parent["status"] != "filled" and (now - timestamp(entry["at"])).total_seconds() > 60:
                self.halt("ENTRY_TIMEOUT")
            elif parent["status"] == "filled":
                positions = snapshot["positions"]
                if any(p["symbol"] != self.policy.symbol for p in positions):
                    raise ValueError("unowned position encountered")
                if not positions:
                    self.halt("POSITION_EXITED")
                else:
                    position = positions[0]
                    expected_sign = 1 if entry["order"]["side"] == "buy" else -1
                    if number(position["qty"]) * expected_sign <= 0 or abs(number(position["qty"])) > 1:
                        self.halt("POSITION_MISMATCH")
                    # Broker-held stop must be visible after a confirmed entry.
                    protected = any(o["symbol"] == self.policy.symbol
                                    and o.get("type") in {"stop", "stop_limit"}
                                    and o.get("side") != entry["order"]["side"]
                                    and number(o["qty"]) >= abs(number(position["qty"]))
                                    for o in snapshot["orders"])
                    if not protected:
                        self.halt("PROTECTION_MISSING")
            if self.journal.last("halt"):
                return "COMPLETE" if self.flatten_step(snapshot) else "FLATTENING"
            return "POSITION_OPEN" if qty > 0 else "ENTRY_WORKING"
        if snapshot["positions"] or snapshot["orders"]:
            raise ValueError("account is not flat before entry")
        if account.get("status") != "ACTIVE" or any(account.get(k) for k in
            ("trading_blocked", "account_blocked", "trade_suspended_by_user")):
            return "ACCOUNT_BLOCKED"
        calendar = self.broker.call("GET", "/v2/calendar?" + urlencode({
            "start": (now.astimezone(NY).date() - timedelta(days=10)).isoformat(), "end": day,
        }))
        current_session = [item for item in calendar if item["date"] == day]
        prior_sessions = [item["date"] for item in calendar if item["date"] < day]
        if len(current_session) != 1 or not prior_sessions:
            raise ValueError("broker calendar unavailable")
        session_open = datetime.fromisoformat(day + "T" + current_session[0]["open"]).replace(tzinfo=NY)
        if self.daily is None:
            self.daily = self.broker.bars(self.policy.symbol, session_open - timedelta(days=60),
                                          session_open, "1Day", self.policy.feed)
        prior_dates = [timestamp(item["t"]).astimezone(NY).date().isoformat()
                       for item in self.daily if timestamp(item["t"]).astimezone(NY).date().isoformat() < day]
        if not prior_dates or max(prior_dates) != max(prior_sessions):
            raise ValueError("prior daily data is stale")
        raw = self.broker.bars(self.policy.symbol, session_open, now, "1Min", self.policy.feed)
        candidate = signal_packet(raw, self.daily, now=now, session_open=session_open, policy=self.policy)
        if candidate is None:
            return "WAITING_FOR_SIGNAL"
        packet, order = candidate
        attempts = [e for e in self.journal.events if e["kind"] == "decision_started" and e["session"] == day]
        if any(e["signal_at"] == packet["signal_at"] for e in attempts):
            return "ALREADY_REVIEWED"
        if len(attempts) >= self.policy.max_adjudications:
            return "COMPLETE"
        asset = self.broker.call("GET", "/v2/assets/" + self.policy.symbol)
        if asset.get("status") != "active" or asset.get("tradable") is not True or asset.get("class") != "us_equity":
            return "ASSET_BLOCKED"
        if order["side"] == "sell" and (not asset.get("shortable") or not asset.get("easy_to_borrow")):
            return "SHORT_BLOCKED"
        price, stop = number(order["limit_price"], positive=True), number(order["stop_loss"]["stop_price"], positive=True)
        if price > min(self.policy.max_notional, equity * 0.01, number(account["buying_power"])):
            return "NOTIONAL_BLOCKED"
        if abs(price - stop) > min(self.policy.max_stop_risk, equity * 0.001):
            return "STOP_RISK_BLOCKED"
        # Reserve this observation before model calls: restart cannot repeat spend/decisions.
        if self.actor is None or self.jev is None:
            raise ModelError("both models are required for a new entry")
        self.journal.append("decision_started", session=day, signal_at=packet["signal_at"],
                            packet=packet, market_inputs={"bars": raw, "daily": self.daily})
        proposal = self.actor.propose(packet)
        identity = {"model": proposal["actor_model"], "digest": proposal["actor_digest"]}
        previous_identity = self.journal.last("actor_identity")
        if previous_identity and previous_identity["identity"] != identity:
            raise ModelError("Actor identity changed across restart")
        if previous_identity is None:
            self.journal.append("actor_identity", identity=identity)
        self.journal.append("actor_proposal", proposal=proposal, packet_hash=canonical_hash(packet))
        if proposal["action"] == "NO_TRADE":
            return "ACTOR_NO_TRADE"
        verdict = self.jev.adjudicate(packet, proposal)
        self.journal.append("adjudication", receipt=verdict.receipt)
        if not verdict.accepted:
            return "JEV_VETO"
        # Reconcile again after both model calls. Jev never bypasses these deterministic checks.
        current = self.broker.snapshot()
        current_now = timestamp(current["clock"]["timestamp"])
        if current_now < now:
            raise ValueError("broker clock moved backwards during adjudication")
        if current["account"]["id"] != account["id"] or current["positions"] or current["orders"]:
            raise ValueError("account changed during adjudication")
        if not current["clock"]["is_open"] or current_now >= timestamp(current["clock"]["next_close"]) - timedelta(minutes=self.policy.close_buffer_minutes):
            return "SESSION_BLOCKED"
        if (current_now - timestamp(packet["signal_at"]) - timedelta(minutes=1)).total_seconds() > self.policy.max_bar_age_seconds:
            return "STALE_AFTER_ADJUDICATION"
        if any(current["account"].get(k) for k in ("trading_blocked", "account_blocked", "trade_suspended_by_user")):
            return "ACCOUNT_BLOCKED"
        current_equity = number(current["account"]["equity"], positive=True)
        if current_equity <= number(current["account"]["last_equity"], positive=True) * (1 - self.policy.max_daily_loss_fraction):
            self.halt("DAILY_LOSS_LIMIT")
            return "COMPLETE"
        if price > min(self.policy.max_notional, current_equity * 0.01, number(current["account"]["buying_power"])) or abs(price - stop) > min(self.policy.max_stop_risk, current_equity * 0.001):
            return "RISK_CHANGED"
        order["client_order_id"] = "athp-" + canonical_hash({
            "account": account["id"], "session": day,
        })[:40]
        self.journal.append("entry_intent", session=day, at=current_now, order=order,
                            proposal_hash=canonical_hash(proposal), verdict_hash=canonical_hash(verdict.receipt))
        # A lost response leaves a durable reservation. Recovery only queries this ID, never resends.
        ack = self.broker.call("POST", "/v2/orders", order)
        self.journal.append("entry_ack", order=ack)
        return "ENTRY_SUBMITTED"
