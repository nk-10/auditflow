"""Scanner node for fetching GitHub repository structure."""

from backend.utils.github_client import GitHubClient
from backend.types import AnalysisState


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
            return state

        # Initialize GitHub client
        client = GitHubClient()

        # Fetch repository structure
        file_structure = client.get_repo_structure(repo_url)

        # Update state
        state["file_structure"] = file_structure
        state["error"] = None

        return state

    except Exception as e:
        state["error"] = f"Scanner error: {str(e)}"
        state["file_structure"] = None
        return state
