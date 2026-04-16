"""LangGraph workflow definition for the Autonomous Codebase Librarian."""

import logging
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from backend.types import AnalysisState
from backend.nodes.scanner_node import scanner_node
from backend.nodes.security_node import security_node
from backend.nodes.human_review_node import human_review_node
from backend.nodes.compiler_node import compiler_node

logger = logging.getLogger(__name__)


def create_graph():
    """Create and configure the LangGraph workflow.

    Returns:
        Compiled StateGraph with all nodes and edges configured
    """
    # Create the state graph
    graph = StateGraph(AnalysisState)

    # Add nodes to the graph
    graph.add_node("scanner", scanner_node)
    graph.add_node("security", security_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("compiler", compiler_node)

    # Define edges
    graph.add_edge(START, "scanner")
    graph.add_edge("scanner", "security")
    graph.add_edge("security", "human_review")

    # Conditional edge: if approved, go to compiler; otherwise end
    def should_compile(state: AnalysisState) -> str:
        """Determine if we should compile the report or end."""
        return "compiler" if state.get("is_approved", False) else END

    graph.add_conditional_edges("human_review", should_compile)
    graph.add_edge("compiler", END)

    # Compile the graph with memory saver for persistence
    checkpointer = MemorySaver()
    compiled_graph = graph.compile(checkpointer=checkpointer)

    logger.info("Workflow graph compiled")
    return compiled_graph


# Global graph instance
workflow = create_graph()
