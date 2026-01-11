"""
Abstract base class for scoring algorithms.
Allows swapping between different approaches (pattern-based, LLM, hybrid).
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseScorer(ABC):
    """Abstract base class for dropship risk scoring."""

    @abstractmethod
    async def score(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Score a product/site for dropship risk.

        Args:
            data: Dictionary containing product/site information

        Returns:
            Dictionary with:
                - score: float (0.0 to 1.0)
                - is_risky: bool
                - evidence: list[str]
                - confidence: float
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """Return the scorer name for logging."""
        pass
