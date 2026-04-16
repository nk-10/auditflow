"""GitHub API client for fetching repository contents."""

import re
from typing import Optional
from github import Github, GithubException
from urllib.parse import urlparse

from backend.config import settings


class GitHubClient:
    """Wrapper around GitHub API for repository analysis."""

    def __init__(self, token: Optional[str] = None):
        """Initialize GitHub client.

        Args:
            token: GitHub personal access token (optional). Uses settings.github_token if not provided.
        """
        self.token = token or settings.github_token
        self.client = Github(self.token) if self.token else Github()

    def parse_repo_url(self, url: str) -> tuple[str, str]:
        """Parse GitHub repository URL into owner and repo name.

        Args:
            url: GitHub repository URL (e.g., https://github.com/owner/repo)

        Returns:
            Tuple of (owner, repo)

        Raises:
            ValueError: If URL is invalid
        """
        # Handle various URL formats
        url = url.strip().rstrip("/")

        # Extract from https://github.com/owner/repo format
        match = re.match(r"(?:https?://)?(?:www\.)?github\.com[:/]([^/]+)/(.+?)(?:\.git)?/?$", url)
        if match:
            return match.group(1), match.group(2)

        raise ValueError(f"Invalid GitHub repository URL: {url}")

    def get_repo_structure(self, repo_url: str) -> dict:
        """Fetch repository structure and file contents.

        Args:
            repo_url: GitHub repository URL

        Returns:
            Dictionary with file structure and contents
        """
        try:
            owner, repo_name = self.parse_repo_url(repo_url)
            repo = self.client.get_user(owner).get_repo(repo_name)

            # Get repository info
            repo_info = {
                "url": repo_url,
                "name": repo.name,
                "owner": owner,
                "description": repo.description,
                "language": repo.language,
                "files": [],
                "total_files_in_repo": 0,
                "analyzed_files": 0,
            }

            # Get all repo contents recursively
            files_analyzed = 0
            max_files = settings.max_files_to_analyze
            max_size = settings.max_file_size_mb * 1024 * 1024

            def get_contents_recursive(directory=""):
                nonlocal files_analyzed

                if files_analyzed >= max_files:
                    return

                try:
                    contents = repo.get_contents(directory)
                    for content in contents:
                        if files_analyzed >= max_files:
                            break

                        # Skip binary files and very large files
                        if content.type == "file":
                            repo_info["total_files_in_repo"] += 1

                            # Check file extension for analysis
                            file_path = content.path
                            if self._should_analyze_file(file_path) and content.size < max_size:
                                try:
                                    # Download file content
                                    file_content = content.decoded_content.decode("utf-8", errors="ignore")
                                    repo_info["files"].append(
                                        {
                                            "path": file_path,
                                            "type": "file",
                                            "size": content.size,
                                            "content": file_content[:50000],  # Limit to 50k chars per file
                                        }
                                    )
                                    files_analyzed += 1
                                except Exception:
                                    pass
                        elif content.type == "dir" and files_analyzed < max_files:
                            # Recursively get directory contents
                            get_contents_recursive(content.path)

                except GithubException as e:
                    if e.status != 409:  # 409 is "Repository is empty"
                        raise

            # Start recursive fetch from root
            get_contents_recursive()
            repo_info["analyzed_files"] = files_analyzed

            return repo_info

        except GithubException as e:
            raise ValueError(f"GitHub API error: {e.data.get('message', str(e))}")
        except Exception as e:
            raise ValueError(f"Error fetching repository: {str(e)}")

    @staticmethod
    def _should_analyze_file(file_path: str) -> bool:
        """Determine if a file should be analyzed based on its extension.

        Args:
            file_path: Path to the file

        Returns:
            True if file should be analyzed, False otherwise
        """
        # Files to analyze
        patterns = [
            r"\.py$",  # Python
            r"\.js$",  # JavaScript
            r"\.ts$",  # TypeScript
            r"\.go$",  # Go
            r"\.java$",  # Java
            r"\.jsx$",  # React
            r"\.tsx$",  # TypeScript React
            r"package\.json$",  # Node.js dependencies
            r"requirements\.txt$",  # Python dependencies
            r"Pipfile$",  # Python dependencies
            r"pyproject\.toml$",  # Python project config
            r"go\.mod$",  # Go dependencies
            r"Gemfile$",  # Ruby dependencies
            r"\.env",  # Environment files
            r"\.config",  # Config files
            r"docker-compose\.yml$",  # Docker config
            r"Dockerfile$",  # Docker
            r"\.yaml$",  # YAML configs
            r"\.yml$",  # YAML configs
            r"\.json$",  # JSON configs
            r"Makefile$",  # Build files
        ]

        for pattern in patterns:
            if re.search(pattern, file_path, re.IGNORECASE):
                return True

        return False
