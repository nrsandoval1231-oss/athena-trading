# ORB-Momentum V1 Campaign Contract

Status: preregistration scaffold; do not score sealed evidence until dataset identity is frozen.

- Universe: 50 liquid U.S. equities selected by a frozen, outcome-blind liquidity rule.
- Window: approximately two full years of regular-session minute bars.
- Opening range: 09:30–09:45 ET.
- Trigger: first completed 1-minute close beyond ORH/ORL; opening-candle color is not a directional gate.
- Both sides: one initial long and one initial short attempt may independently qualify per symbol/session; one continuation re-entry per direction.
- Initial stop: long `max(ORL, entry - 1*ATR)`; short `min(ORH, entry + 1*ATR)`, subject to minimum tradable distance.
- R: frozen from entry to initial stop.
- Re-entry impulse: actual entry to peak favorable excursion.
- Promotion evidence floor: 300 paired eligible episodes. Fewer means `INSUFFICIENT_EVIDENCE`.
- Partition identities/hashes must be frozen before outcome inspection.
- Sealed holdout cannot be used for tuning.
- Explicit costs/slippage required.
- No overnight positions.
