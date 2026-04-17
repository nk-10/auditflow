"""Security analyzer using Groq LLM for vulnerability detection."""

import json
import logging
import re
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from groq import APIStatusError as GroqAPIStatusError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    RetryError,
)

from backend.config import settings

logger = logging.getLogger(__name__)


def _is_retryable_llm_error(exc: BaseException) -> bool:
    """Return False for errors where retrying the same payload will never succeed."""
    if isinstance(exc, GroqAPIStatusError) and exc.status_code == 413:
        return False  # payload too large — retrying won't shrink the prompt
    return True


class SecurityAnalyzer:
    """Analyzes repository files for security vulnerabilities using Groq."""

    def __init__(self):
        """Initialize the security analyzer with Groq LLM."""
        self.llm = ChatGroq(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            groq_api_key=settings.groq_api_key,
            request_timeout=60,
        )

    def analyze_files(self, files: list[dict]) -> list[dict]:
        """Analyze files for security vulnerabilities.

        Args:
            files: List of file dictionaries with 'path' and 'content' keys

        Returns:
            List of security findings
        """
        if not files:
            logger.info("SecurityAnalyzer called with no files to analyze")
            return []

        logger.info("SecurityAnalyzer analyzing %d files", len(files))
        # Prepare file summary for analysis
        file_summary = self._prepare_file_summary(files)

        # Create comprehensive security analysis prompt
        prompt = self._create_analysis_prompt(file_summary)
        logger.debug("SecurityAnalyzer generated analysis prompt of length %d", len(prompt))

        # Get LLM analysis — wrapped with retry for transient failures
        try:
            response = self._invoke_llm_with_retry([HumanMessage(content=prompt)])
        except RetryError as exc:
            raise RuntimeError(
                "LLM analysis failed after 3 attempts — Groq may be unavailable or rate-limited"
            ) from exc
        logger.info("SecurityAnalyzer received LLM response")

        # Parse findings from response
        findings = self._parse_findings(response.content)
        logger.info("SecurityAnalyzer parsed %d findings", len(findings))

        return findings

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception(_is_retryable_llm_error),
        reraise=False,
    )
    def _invoke_llm_with_retry(self, messages: list) -> object:
        """Invoke the LLM with automatic retry on transient failures.

        Retries up to 3 times with exponential backoff (2s → 30s max).
        Skips retry for HTTP 413 (payload too large) since the prompt
        won't change between attempts.
        """
        return self.llm.invoke(messages)

    def _prepare_file_summary(self, files: list[dict]) -> str:
        """Prepare a summary of files for analysis.

        Args:
            files: List of file dictionaries

        Returns:
            Formatted file summary string
        """
        summary_parts = []

        for file_dict in files[: settings.llm_max_files]:
            path = file_dict.get("path", "unknown")
            content = file_dict.get("content", "")

            if len(content) > settings.llm_max_chars_per_file:
                content = content[: settings.llm_max_chars_per_file] + "\n... [truncated]"

            summary_parts.append(f"FILE: {path}\n{content}\n" + "=" * 40)

        return "\n\n".join(summary_parts)

    def _create_analysis_prompt(self, file_summary: str) -> str:
        """Create the security analysis prompt.

        Args:
            file_summary: Summary of repository files

        Returns:
            Formatted prompt for security analysis
        """
        prompt = f"""You are a professional security auditor analyzing a GitHub repository for vulnerabilities.

Analyze the following repository files and identify security issues:

{file_summary}

Please identify and report on:

1. **Code Vulnerabilities:**
   - SQL injection risks
   - Cross-site scripting (XSS) vulnerabilities
   - Command injection risks
   - Hardcoded secrets or API keys
   - Insecure parameter handling
   - Unsafe deserialization

2. **Dependency Vulnerabilities:**
   - Outdated or known vulnerable packages
   - Missing security patches
   - Insecure package versions

3. **Configuration Issues:**
   - Exposed API keys in config files
   - Debug mode enabled in production
   - Overly permissive access controls
   - Missing authentication/authorization
   - Insecure defaults

4. **Other Security Concerns:**
   - Weak cryptography
   - Unsafe file operations
   - Missing input validation

Provide your findings in the following JSON format ONLY (no markdown, no explanations):
{{
  "findings": [
    {{
      "type": "vulnerability|warning|info",
      "severity": "critical|high|medium|low",
      "title": "Brief title",
      "description": "Detailed description of the issue",
      "file": "path/to/file.ext or package.json",
      "line_reference": "line X or 'N/A'",
      "cwe": "CWE-XXX or 'N/A'",
      "recommendation": "How to fix or mitigate this issue"
    }}
  ],
  "summary": "Overall security posture summary"
}}

Return ONLY valid JSON, no other text."""

        return prompt

    def _parse_findings(self, response_text: str) -> list[dict]:
        """Parse security findings from LLM response.

        Args:
            response_text: Raw response text from LLM

        Returns:
            List of parsed security findings
        """
        try:
            # Extract JSON from response (in case there's extra text)
            json_match = re.search(r"\{[\s\S]*\}", response_text)
            if not json_match:
                return self._create_default_finding("Failed to parse analysis", response_text)

            json_str = json_match.group(0)
            data = json.loads(json_str)

            findings = data.get("findings", [])

            # Validate and normalize findings
            normalized_findings = []
            for finding in findings:
                if isinstance(finding, dict):
                    normalized_findings.append(
                        {
                            "type": finding.get("type", "vulnerability"),
                            "severity": finding.get("severity", "medium"),
                            "title": finding.get("title", "Unknown Issue"),
                            "description": finding.get("description", ""),
                            "file": finding.get("file", "unknown"),
                            "line_reference": finding.get("line_reference", "N/A"),
                            "cwe": finding.get("cwe", "N/A"),
                            "recommendation": finding.get("recommendation", "Review and address this issue"),
                        }
                    )

            return normalized_findings

        except json.JSONDecodeError:
            logger.error("SecurityAnalyzer JSON parsing error for response: %s", response_text)
            return self._create_default_finding("JSON parsing error", response_text)
        except Exception as e:
            logger.error("SecurityAnalyzer failed to parse findings: %s", e, exc_info=True)
            return self._create_default_finding(f"Parsing error: {str(e)}", response_text)

    @staticmethod
    def _create_default_finding(error_msg: str, details: str) -> list[dict]:
        """Create a default finding for analysis errors.

        Args:
            error_msg: Error message
            details: Detailed information

        Returns:
            List with single error finding
        """
        return [
            {
                "type": "info",
                "severity": "low",
                "title": error_msg,
                "description": f"Analysis encountered an error: {details[:500]}",
                "file": "N/A",
                "line_reference": "N/A",
                "cwe": "N/A",
                "recommendation": "Please review the repository manually or try again.",
            }
        ]
