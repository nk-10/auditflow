"""Compiler node for generating the final security analysis report."""

import logging
from backend.utils.report_generator import ReportGenerator
from backend.types import AnalysisState

logger = logging.getLogger(__name__)


def compiler_node(state: AnalysisState) -> AnalysisState:
    """Generate the final security analysis report.

    Args:
        state: Current workflow state

    Returns:
        Updated state with final report
    """
    try:
        file_structure = state.get("file_structure", {})
        findings = state.get("security_findings", [])
        repo_url = state.get("repo_url", "")
        is_approved = state.get("is_approved", False)

        repo_name = file_structure.get("name", "Unknown Repository")
        analyzed_files = file_structure.get("analyzed_files", 0)
        total_files = file_structure.get("total_files_in_repo", 0)

        logger.info(
            "Compiler node generating report for %s with %d findings",
            repo_url,
            len(findings),
        )
        # Generate report
        report = ReportGenerator.generate_report(
            repo_url=repo_url,
            repo_name=repo_name,
            findings=findings,
            is_approved=is_approved,
            analyzed_files=analyzed_files,
            total_files=total_files,
        )

        # Update state with report
        state["analysis_report"] = report
        state["error"] = None
        logger.info("Compiler node completed report generation for %s", repo_url)

        return state

    except Exception as e:
        state["error"] = f"Report generation error: {str(e)}"
        state["analysis_report"] = "Failed to generate report"
        logger.error("Compiler node failed: %s", e, exc_info=True)
        return state
