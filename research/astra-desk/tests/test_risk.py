from astra_desk.accounting import CapitalBooks
from astra_desk.contract import load_contract
from astra_desk.risk import evaluate_hard_limits
from astra_desk.types import Position


def _books() -> CapitalBooks:
    return CapitalBooks(starting_equity=500.0, currency="USD")


def test_risk_officer_approves_inside_limits() -> None:
    limits = load_contract().hard_limits
    result = evaluate_hard_limits(
        books=_books(),
        limits=limits,
        proposed_notional=20.0,
        correlation_group="index-beta",
    )
    assert result.approved is True
    assert result.veto_reason is None
    assert result.exposure_after == 4.0


def test_risk_officer_veto_single_position() -> None:
    limits = load_contract().hard_limits
    result = evaluate_hard_limits(
        books=_books(),
        limits=limits,
        proposed_notional=80.0,
        correlation_group="index-beta",
    )
    assert result.approved is False
    assert result.veto_reason == "max_single_position_pct"


def test_risk_officer_veto_total_exposure() -> None:
    books = _books()
    books.open_position(
        Position(symbol="A", notional_usd=150.0, correlation_group="g1")
    )
    books.open_position(
        Position(symbol="B", notional_usd=150.0, correlation_group="g2")
    )
    limits = load_contract().hard_limits
    result = evaluate_hard_limits(
        books=books,
        limits=limits,
        proposed_notional=40.0,
        correlation_group="g3",
    )
    assert result.approved is False
    assert result.veto_reason == "max_total_exposure_pct"


def test_risk_officer_veto_correlated_positions() -> None:
    books = _books()
    books.open_position(Position(symbol="A", notional_usd=20.0, correlation_group="beta"))
    books.open_position(Position(symbol="B", notional_usd=20.0, correlation_group="beta"))
    limits = load_contract().hard_limits
    result = evaluate_hard_limits(
        books=books,
        limits=limits,
        proposed_notional=20.0,
        correlation_group="beta",
    )
    assert result.approved is False
    assert result.veto_reason == "max_correlated_positions"


def test_risk_officer_veto_daily_loss() -> None:
    books = _books()
    books.apply_daily_loss_mark(5.0)
    limits = load_contract().hard_limits
    result = evaluate_hard_limits(
        books=books,
        limits=limits,
        proposed_notional=20.0,
        correlation_group="beta",
    )
    assert result.approved is False
    assert result.veto_reason == "daily_loss_limit_pct"
