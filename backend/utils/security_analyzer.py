"""Security analyzer using Groq LLM for vulnerability detection."""

import json
import logging
import re
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from groq import APIStatusError as GroqAPIStatusError
from groq import RateLimitError as GroqRateLimitError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    RetryError,
)

from backend.config import settings

logger = logging.getLogger(__name__)

_TPD_WAIT_RE = re.compile(
    r"(?:try again in|retry (?:after|in))\s+"
    r"(\d+h\d+m[\d.]+s|\d+h\d+m|\d+h[\d.]+s|\d+h|\d+m[\d.]+s|\d+m|\d+[\d.]*s)",
    re.IGNORECASE,
)


def _is_tpd_error(exc: BaseException) -> bool:
    if not isinstance(exc, GroqRateLimitError):
        return False
    msg = str(exc).lower()
    return "tokens per day" in msg or " tpd" in msg


def _extract_wait_time(exc: BaseException) -> str | None:
    m = _TPD_WAIT_RE.search(str(exc))
    return m.group(1) if m else None


def _is_tpm_error(exc: BaseException) -> bool:
    if not isinstance(exc, GroqRateLimitError):
        return False
    msg = str(exc).lower()
    return "tokens per minute" in msg or " tpm" in msg


def _is_retryable_llm_error(exc: BaseException) -> bool:
    """Return False for errors where retrying the same payload will never succeed."""
    if isinstance(exc, GroqAPIStatusError) and exc.status_code == 413:
        return False  # payload too large — retrying won't shrink the prompt
    if _is_tpd_error(exc):
        return False  # daily token quota — won't recover within seconds
    if _is_tpm_error(exc):
        return False  # per-minute token quota — retrying immediately won't help
    return True


class SecurityAnalyzer:
    """Analyzes repository files for security vulnerabilities using Groq."""

    def __init__(self):
        """Initialize the security analyzer with primary and fallback Groq LLMs."""
        self.llm = ChatGroq(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            groq_api_key=settings.groq_api_key,
            request_timeout=60,
        )
        self.llm_fallback = ChatGroq(
            model=settings.llm_model_fallback,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_fallback_max_tokens,
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
        file_summary = self._prepare_file_summary(files)
        prompt = self._create_analysis_prompt(file_summary)
        logger.debug("SecurityAnalyzer generated analysis prompt of length %d", len(prompt))

        messages = [HumanMessage(content=prompt)]

        # Primary model
        try:
            response = self._invoke_llm_with_retry(messages)
        except RetryError as exc:
            raise RuntimeError(
                f"LLM analysis failed after 3 attempts on primary model "
                f"({settings.llm_model}) — Groq may be unavailable or rate-limited"
            ) from exc
        except GroqRateLimitError as exc:
            # TPD on primary: predicate returned False, tenacity raised immediately
            wait = _extract_wait_time(exc)
            wait_msg = f" Try again in {wait}." if wait else ""
            logger.warning(
                "Primary model %s hit daily token limit (TPD).%s Switching to %s.",
                settings.llm_model, wait_msg, settings.llm_model_fallback,
            )
            # Fallback model — use a compact prompt to fit within the smaller TPM budget
            compact_summary = self._prepare_file_summary(
                files,
                max_files=settings.llm_fallback_max_files,
                max_chars=settings.llm_fallback_max_chars_per_file,
            )
            fallback_messages = [HumanMessage(content=self._create_analysis_prompt(compact_summary))]
            logger.debug(
                "SecurityAnalyzer fallback prompt length %d (compact: %d files × %d chars)",
                len(fallback_messages[0].content),
                settings.llm_fallback_max_files,
                settings.llm_fallback_max_chars_per_file,
            )
            try:
                response = self._invoke_llm_fallback_with_retry(fallback_messages)
                logger.info("Fallback model %s succeeded.", settings.llm_model_fallback)
            except RetryError as exc2:
                raise RuntimeError(
                    f"LLM analysis failed after 3 attempts on fallback model "
                    f"({settings.llm_model_fallback}) — Groq may be rate-limited"
                ) from exc2
            except GroqRateLimitError as exc2:
                wait2 = _extract_wait_time(exc2)
                wait_msg2 = f" Try again in {wait2}." if wait2 else ""
                limit_kind = "per-minute token limit (TPM)" if _is_tpm_error(exc2) else "daily token limit (TPD)"
                raise RuntimeError(
                    f"Fallback model {settings.llm_model_fallback} hit its {limit_kind}.{wait_msg2} "
                    f"Primary model {settings.llm_model} also exhausted."
                ) from exc2

        logger.info("SecurityAnalyzer received LLM response")
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
        Skips retry for HTTP 413 (payload too large) and TPD rate limits.
        """
        return self.llm.invoke(messages)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception(_is_retryable_llm_error),
        reraise=False,
    )
    def _invoke_llm_fallback_with_retry(self, messages: list) -> object:
        """Invoke the fallback LLM with the same retry policy as the primary."""
        return self.llm_fallback.invoke(messages)

    def _prepare_file_summary(
        self,
        files: list[dict],
        max_files: int | None = None,
        max_chars: int | None = None,
    ) -> str:
        """Prepare a summary of files for analysis.

        Args:
            files: List of file dictionaries
            max_files: Override for maximum number of files (defaults to settings.llm_max_files)
            max_chars: Override for max chars per file (defaults to settings.llm_max_chars_per_file)

        Returns:
            Formatted file summary string
        """
        limit_files = max_files if max_files is not None else settings.llm_max_files
        limit_chars = max_chars if max_chars is not None else settings.llm_max_chars_per_file
        summary_parts = []

        for file_dict in files[:limit_files]:
            path = file_dict.get("path", "unknown")
            content = file_dict.get("content", "")

            if len(content) > limit_chars:
                content = content[:limit_chars] + "\n... [truncated]"

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
            # Find the first { and decode from there; raw_decode ignores trailing text
            start = response_text.find("{")
            if start == -1:
                return self._create_default_finding("Failed to parse analysis", response_text)

            data, _ = json.JSONDecoder().raw_decode(response_text, start)

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
