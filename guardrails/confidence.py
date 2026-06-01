"""ConfidenceChecker — escalates when an agent's score falls below threshold.

Used by Orchestrator before HITL gates. If escalation is required, the gate
upgrades to explicit-CONFIRM regardless of amount.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.settings import settings


@dataclass
class ConfidenceDecision:
    pass_: bool  # True = above threshold, proceed normally
    score: float
    threshold: float
    escalate: bool  # True = must show user a warning before any gate


class ConfidenceChecker:
    def __init__(self, threshold: float | None = None):
        self.threshold = threshold if threshold is not None else settings.hitl.confidence_escalation

    def check(self, score: float) -> ConfidenceDecision:
        below = score < self.threshold
        return ConfidenceDecision(
            pass_=not below,
            score=score,
            threshold=self.threshold,
            escalate=below,
        )
