"""Paper-trading package. No live-money authority.

PAPER execution may be granted by an operator. It stays off unless configured.
LIVE is not a value. The AAT holdout, claim contract, and TRAINING evaluator
are not this package and are not touched by a paper session.
"""
from .alpaca import (
    PAPER_API_BASE, AlpacaPaperBroker, AlpacaPaperError, MissingCredentialsError,
)
from .demo import DEMO_CONSTITUTION, DEMO_EQUITY, DEMO_PARAMETERS, DEMO_SYMBOL, demo_bars
from .fixture import FixtureBroker
from .session import (
    PaperAction, PaperSessionResult, cancel_order, decisions_from_symbol_session,
    load_paper_config, result_as_dict, run_dry_run, run_orb_m_paper_session,
    run_paper_session, track_order,
)
