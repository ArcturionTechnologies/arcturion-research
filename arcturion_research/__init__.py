"""ArcturionResearch: multi-source search with merged, deduplicated results."""
from .router import INTENTS, ROUTES, plan, research, set_classifier, set_summarizer

__all__ = ["INTENTS", "ROUTES", "plan", "research", "set_classifier", "set_summarizer"]
__version__ = "0.1.0"
