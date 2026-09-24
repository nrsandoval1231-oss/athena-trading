"""Run an active Jev-adjudicated session on Alpaca paper with current market data."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from athena_trading.athena_adapter.jev import JEV_MODEL, JevAdjudicator, request_json
from athena_trading.execution.authority import parse_authority, require_paper
from athena_trading.paper.journal import PaperJournal
from athena_trading.paper.live import ActivePaperPilot, LivePaperAPI, OllamaActor, PilotPolicy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="one explicit US equity symbol")
    parser.add_argument("--authority", required=True, choices=["PAPER"])
    parser.add_argument("--feed", choices=["iex", "sip"], default="iex")
    parser.add_argument("--journal", type=Path, required=True,
                        help="persistent SQLite path; reuse on restart, never reset during a session")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="check connections without placing orders")
    mode.add_argument("--run", action="store_true", help="actively adjudicate and execute a paper session")
    args = parser.parse_args(argv)
    require_paper(parse_authority(args.authority))
    missing = [name for name in ("ALPACA_API_KEY", "ALPACA_API_SECRET")
               if not os.environ.get(name, "").strip()]
    if missing:
        print(json.dumps({"ok": False, "missing_environment": missing, "orders_submitted": 0}))
        return 2
    journal = None
    pilot = None
    stopped = False

    def stop_requested(signum, frame):
        nonlocal stopped
        stopped = True

    try:
        policy = PilotPolicy(symbol=args.symbol, feed=args.feed)
        broker = LivePaperAPI.from_env()
        if args.run:
            journal = PaperJournal(args.journal)
            # Existing positions must remain manageable even when either model is offline.
            pilot = ActivePaperPilot(broker, None, None, journal, policy)
        if pilot is None or not pilot._entry():
            actor = OllamaActor(policy.actor_model)
            jev = JevAdjudicator(os.environ.get("TYPESAFE_API_KEY", ""),
                                support_min=policy.support_min, confidence_min=policy.confidence_min)
            # Authenticate Jev with a read-only request, not a synthetic inference.
            models = request_json("GET", "https://api.typesafe.ai/v1/models", {
                "Authorization": "Bearer " + os.environ["TYPESAFE_API_KEY"],
            }, timeout=5)
            if not isinstance(models, dict) or not isinstance(models.get("models"), list):
                raise ValueError("TypeSafe model-list response is invalid")
            if pilot is not None:
                pilot.actor, pilot.jev = actor, jev
        snapshot = broker.snapshot()
        if args.preflight:
            print(json.dumps({
                "ok": True, "mode": "PAPER", "market_open": snapshot["clock"]["is_open"],
                "positions": len(snapshot["positions"]), "open_orders": len(snapshot["orders"]),
                "actor": actor.model, "actor_digest": actor.digest, "jev": JEV_MODEL,
                "symbol": policy.symbol, "feed": policy.feed, "orders_submitted": 0,
            }))
            return 0
        signal.signal(signal.SIGTERM, stop_requested)
        signal.signal(signal.SIGINT, stop_requested)
        previous_status = None
        while not stopped:
            status = pilot.tick()
            if status != previous_status:
                print(json.dumps({"mode": "PAPER", "status": status}), flush=True)
                previous_status = status
            if status == "COMPLETE":
                return 0
            time.sleep(5)
        pilot.halt("OPERATOR_STOP")
    except Exception as exc:
        # No provider payload, credentials, or traceback in operator output.
        print(json.dumps({"ok": False, "error_type": type(exc).__name__,
                          "error": str(exc)[:250]}), file=sys.stderr, flush=True)
        if pilot is not None:
            try:
                pilot.halt("RUNNER_ERROR")
            except Exception:
                pass  # still attempt broker cleanup if the journal write failed
    finally:
        if pilot is not None and pilot._entry() and not journal.last("flat_confirmed"):
            for _ in range(24):
                try:
                    if pilot.flatten_step(broker.snapshot()):
                        print(json.dumps({"status": "FLAT_CONFIRMED", "mode": "PAPER"}), flush=True)
                        break
                except Exception:
                    pass
                time.sleep(5)
            else:
                print(json.dumps({"ok": False, "status": "UNRESOLVED_PAPER_EXPOSURE",
                                  "action": "Inspect the Alpaca paper account and close remaining exposure."}),
                      file=sys.stderr, flush=True)
        if journal is not None:
            journal.close()
    return 2


if __name__ == "__main__":
    sys.exit(main())
