"""Type definitions for the Autonomous Codebase Librarian."""

from typing import TypedDict, Optional


class AnalysisState(TypedDict):
    """State for the code analysis workflow."""

    repo_url: str
    file_structure: Optional[dict]
    security_findings: list[dict]
    analysis_report: str
    is_approved: bool
    thread_id: str
    error: Optional[str]
