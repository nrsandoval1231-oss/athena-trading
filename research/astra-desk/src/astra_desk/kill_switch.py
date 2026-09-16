"""Independent kill switch. Reenable is manual_human_only."""

from __future__ import annotations

from astra_desk.accounting import CapitalBooks
from astra_desk.errors import KillSwitchError
from astra_desk.types import KillSwitchState


class KillSwitch:
    ACTION = "flatten_all_and_halt"
    REENABLE = "manual_human_only"

    def __init__(self, *, drawdown_pct: float, daily_loss_limit_pct: float) -> None:
        self.drawdown_pct = float(drawdown_pct)
        self.daily_loss_limit_pct = float(daily_loss_limit_pct)
        self._halted = False
        self._reason: str | None = None
        self._armed = True

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def armed(self) -> bool:
        """Present, not tripped, and armed for flatten_all_and_halt on breach."""
        return self._armed and not self._halted

    def state(self) -> KillSwitchState:
        return KillSwitchState(
            halted=self._halted,
            reason=self._reason,
            action=self.ACTION if self._halted else None,
            reenable=self.REENABLE,
            armed=self.armed,
        )

    def armed_status(self) -> tuple[bool, str]:
        if not self._armed:
            return False, "kill_switch_not_armed"
        if self._halted:
            return False, "kill_switch_tripped"
        if self.ACTION != "flatten_all_and_halt":
            return False, "kill_switch_action_not_flatten_all_and_halt"
        if self.REENABLE != "manual_human_only":
            return False, "kill_switch_reenable_not_manual_human_only"
        return True, "kill_switch_armed"

    def arm(self) -> None:
        if self._halted:
            raise KillSwitchError("cannot arm while halted; reenable is manual_human_only")
        self._armed = True

    def disarm(self) -> None:
        self._armed = False

    def should_trigger(self, books: CapitalBooks) -> str | None:
        reasons: list[str] = []
        if books.current_drawdown_pct >= self.drawdown_pct:
            reasons.append("drawdown_pct >= 8.0")
        if books.daily_loss_pct >= self.daily_loss_limit_pct:
            reasons.append("daily_loss_limit_breach")
        if not reasons:
            return None
        return " OR ".join(reasons)

    def trip(self, books: CapitalBooks, reason: str, *, broker: object | None = None) -> KillSwitchState:
        if broker is not None:
            flatten = getattr(broker, "flatten", None)
            if callable(flatten):
                flatten()
        books.flatten_all()
        self._halted = True
        self._reason = reason
        return self.state()

    def poll(self, books: CapitalBooks, *, broker: object | None = None) -> KillSwitchState:
        if self._halted:
            return self.state()
        reason = self.should_trigger(books)
        if reason:
            return self.trip(books, reason, broker=broker)
        return self.state()

    def reenable(self, *, human_confirmed: bool) -> None:
        if not human_confirmed:
            raise KillSwitchError("reenable requires human_confirmed=True (manual_human_only)")
        self._halted = False
        self._reason = None

    def restore_snapshot(
        self,
        *,
        halted: bool,
        reason: str | None,
        armed: bool,
    ) -> None:
        """Reload persisted kill-switch flags. Reenable remains manual_human_only."""
        self._halted = bool(halted)
        self._reason = reason
        self._armed = bool(armed)
