from astra_desk.agents.base import Agent, AgentContext
from astra_desk.agents.rules import (
    AdversarialSkeptic,
    DemandAuthenticityReader,
    InvalidationExit,
    VolumeLeadScout,
)
from astra_desk.agents.stubs import (
    BrokerAgent,
    LedgerMemoryAgent,
    RiskOfficerAgent,
    build_stub_agents,
)
from astra_desk.contract import DeskContract


def build_default_agents(contract: DeskContract) -> dict[str, object]:
    """Rule-based voting agents plus constraint broker, ledger, and risk officer."""
    agents = [
        VolumeLeadScout(),
        DemandAuthenticityReader(),
        BrokerAgent(),
        InvalidationExit(),
        LedgerMemoryAgent(),
        AdversarialSkeptic(),
        RiskOfficerAgent(),
    ]
    by_id = {a.id: a for a in agents}
    missing = sorted(set(contract.agent_ids()) - set(by_id))
    if missing:
        raise RuntimeError(f"default agents missing contract ids: {missing}")
    return by_id


__all__ = [
    "Agent",
    "AgentContext",
    "build_default_agents",
    "build_stub_agents",
]
