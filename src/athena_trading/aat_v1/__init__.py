"""Safe, training-only AAT-V1 planning boundary."""

from .training import (
    AATValidationError,
    TrainingRow,
    load_training_projection,
    plan_training_run,
)
from .opportunities import OpportunityCompileError, compile_training_opportunities

__all__ = ["AATValidationError", "OpportunityCompileError", "TrainingRow", "compile_training_opportunities", "load_training_projection", "plan_training_run"]
