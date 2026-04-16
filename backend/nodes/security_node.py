"""Security analysis node using Groq for vulnerability detection."""

from backend.utils.security_analyzer import SecurityAnalyzer
from backend.types import AnalysisState


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
            return state

        # Extract files from structure
        files = file_structure.get("files", [])
        if not files:
            state["security_findings"] = []
            state["error"] = None
            return state

        # Initialize security analyzer
        analyzer = SecurityAnalyzer()

        # Analyze files
        findings = analyzer.analyze_files(files)

        # Update state
        state["security_findings"] = findings
        state["error"] = None

        return state

    except Exception as e:
        state["error"] = f"Security analysis error: {str(e)}"
        state["security_findings"] = []
        return state
