# Historical Source Provenance

The following implementation lineage originated in `nrsandoval1231-oss/Athena` before the domain split.

## Trading-owned source families

- `src/athena_lab/massive*.py` → market-data/provider lineage
- `src/athena_lab/alpaca_corporate_actions.py` → Alpaca market/reference lineage
- paper/broker adapters and `configs/athena_paper_risk_v1.json` → paper execution/risk lineage
- ORB observation/scanning scripts → ORB strategy lineage
- fill/slippage/timing diagnostics → trading execution research lineage
- APDE trading state machine → position-management lineage

## Deliberately not copied as trading ownership

`models.py`, `episodes.py`, generic evidence semantics, Arena, Gauntlet, policy lineage and validation authority remain Athena Core concepts even when their earliest implementation used trading terminology.

Historical files in Athena are compatibility snapshots. They should not receive new trading features after this split.
