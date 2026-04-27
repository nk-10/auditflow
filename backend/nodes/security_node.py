"""Security analysis node using Groq for vulnerability detection."""

import logging
from backend.utils.security_analyzer import SecurityAnalyzer
from backend.types import AnalysisState

logger = logging.getLogger(__name__)


def security_node(state: AnalysisState) -> AnalysisState:
    """Analyze repository files for security vulnerabilities.

    Args:
        state: Current workflow state

    Returns:
        Updated state with security findings
    """
    try:
        file_structure = state.get("file_structure")
        if not file_structure:
            state["error"] = "No file structure available for analysis"
            state["security_findings"] = []
            logger.warning("Security node skipped because no file structure is available")
            return state

        # Extract files from structure
        files = file_structure.get("files", [])
        if not files:
            state["security_findings"] = []
            state["error"] = None
            logger.info("Security node found no files to analyze")
            return state

        logger.info("Security node analyzing %d files", len(files))
        # Initialize security analyzer
        analyzer = SecurityAnalyzer()

        # Analyze files
        findings = analyzer.analyze_files(files)

        # Update state
        state["security_findings"] = findings
        state["error"] = None
        logger.info(
            "Security node completed analysis with %d findings",
            len(findings),
        )

        return state

    except Exception as e:
        error_msg = str(e)
        state["error"] = f"Security analysis error: {error_msg}"
        state["security_findings"] = []
        logger.error("Security node failed (%s): %s", type(e).__name__, error_msg, exc_info=True)
        return state
