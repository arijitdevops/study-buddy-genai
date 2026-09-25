"""Tools the agent can call: web search, Wikipedia, document retrieval, maths."""

from app.agents.tools.calculator import CalculatorError, evaluate as calculate
from app.agents.tools.doc_retrieval import DocRetrievalTool, get_doc_retrieval_tool
from app.agents.tools.web_search import (
    WebSearchError,
    WebSearchNotConfiguredError,
    WebSearchTool,
    get_web_search_tool,
)
from app.agents.tools.wikipedia import WikipediaError, WikipediaTool, get_wikipedia_tool

__all__ = [
    "CalculatorError",
    "DocRetrievalTool",
    "WebSearchError",
    "WebSearchNotConfiguredError",
    "WebSearchTool",
    "WikipediaError",
    "WikipediaTool",
    "calculate",
    "get_doc_retrieval_tool",
    "get_web_search_tool",
    "get_wikipedia_tool",
]
