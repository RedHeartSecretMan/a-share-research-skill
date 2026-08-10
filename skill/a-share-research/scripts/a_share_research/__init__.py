"""Deterministic runtime for the A-share research skill."""

from .research_result import UnifiedResearchResult
from .research_runtime import ResearchRuntime, research

__all__ = ["ResearchRuntime", "UnifiedResearchResult", "research"]
