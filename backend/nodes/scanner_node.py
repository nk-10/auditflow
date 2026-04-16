"""Scanner node for fetching GitHub repository structure."""

import logging
from backend.utils.github_client import GitHubClient
from backend.types import AnalysisState

logger = logging.getLogger(__name__)


def scanner_node(state: AnalysisState) -> AnalysisState:
    """Fetch repository structure from GitHub.

    Args:
        state: Current workflow state

    Returns:
        Updated state with file structure
    """
    try:
        repo_url = state.get("repo_url", "")
        if not repo_url:
            state["error"] = "No repository URL provided"
            logger.warning("Scanner node encountered missing repo_url")
            return state

        logger.info("Scanner node starting repository scan for %s", repo_url)
        # Initialize GitHub client
        client = GitHubClient()

        # Fetch repository structure
        file_structure = client.get_repo_structure(repo_url)

        # Update state
        state["file_structure"] = file_structure
        state["error"] = None
        logger.info(
            "Scanner node completed repository scan for %s: %d files found",
            repo_url,
            len(file_structure.get("files", [])),
        )

        return state

    except Exception as e:
        state["error"] = f"Scanner error: {str(e)}"
        state["file_structure"] = None
        logger.error("Scanner node failed for repo_url=%s: %s", repo_url, e, exc_info=True)
        return state
