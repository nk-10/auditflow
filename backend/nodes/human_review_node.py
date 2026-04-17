"""Human review node that interrupts the workflow for approval."""

import logging
from langgraph.types import interrupt
from backend.types import AnalysisState

logger = logging.getLogger(__name__)


def human_review_node(state: AnalysisState) -> AnalysisState:
    """Interrupt for human review and approval of security findings.

    Args:
        state: Current workflow state

    Returns:
        Updated state with approval decision (updated by frontend after interrupt)
    """
    findings = state.get("security_findings", [])

    # Calculate summary statistics
    critical_count = sum(1 for f in findings if f.get("severity") == "critical")
    high_count = sum(1 for f in findings if f.get("severity") == "high")
    medium_count = sum(1 for f in findings if f.get("severity") == "medium")
    low_count = sum(1 for f in findings if f.get("severity") == "low")

    # Prepare interrupt message
    findings_summary = f"""
Security Analysis Complete

Repository: {state.get('repo_url', 'Unknown')}
Total Findings: {len(findings)}
- Critical: {critical_count}
- High: {high_count}
- Medium: {medium_count}
- Low: {low_count}

Please review the findings and approve or reject the report generation.
"""

    logger.info(
        "Human review required for repo_url=%s with total findings=%d",
        state.get("repo_url", "unknown"),
        len(findings),
    )
    # Interrupt with findings for human review; returns the Command(resume=...) payload
    decision = interrupt(
        {
            "type": "human_review",
            "repo_url": state.get("repo_url", ""),
            "findings_count": {
                "critical": critical_count,
                "high": high_count,
                "medium": medium_count,
                "low": low_count,
                "total": len(findings),
            },
            "findings": findings,
            "message": findings_summary.strip(),
        }
    )

    # decision holds the value passed via Command(resume={"is_approved": ...})
    is_approved = decision.get("is_approved", False) if isinstance(decision, dict) else False
    return {**state, "is_approved": is_approved}
