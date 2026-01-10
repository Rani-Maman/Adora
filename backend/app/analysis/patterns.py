"""
Pattern-based dropship detection (placeholder).
This will be expanded with actual detection logic later.
"""
from typing import Any
from app.analysis.base import BaseScorer


class PatternScorer(BaseScorer):
    """Pattern-based dropship risk scorer."""
    
    async def score(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Score based on pattern matching.
        
        TODO: Implement actual patterns:
        - Long shipping times
        - Missing contact info
        - Generic product descriptions
        - Price patterns
        """
        # Placeholder implementation
        return {
            "score": 0.0,
            "is_risky": False,
            "evidence": [],
            "confidence": 0.0,
            "scorer": self.get_name()
        }
    
    def get_name(self) -> str:
        return "pattern_scorer"
