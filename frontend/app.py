"""Streamlit frontend for the Autonomous Codebase Librarian."""

import html
import logging
import os
import time
from datetime import datetime

import requests
import streamlit as st

logger = logging.getLogger(__name__)

# Page configuration
st.set_page_config(
    page_title="Autonomous Codebase Librarian",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Custom CSS
st.markdown(
    """
    <style>
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 20px;
        border-radius: 10px;
        color: white;
        margin-bottom: 20px;
    }
    .finding-critical {
        background-color: #fee;
        border-left: 4px solid #c33;
        padding: 10px;
        border-radius: 5px;
        margin: 10px 0;
    }
    .finding-high {
        background-color: #fef3cd;
        border-left: 4px solid #ff9800;
        padding: 10px;
        border-radius: 5px;
        margin: 10px 0;
    }
    .finding-medium {
        background-color: #e8f5e9;
        border-left: 4px solid #ffc107;
        padding: 10px;
        border-radius: 5px;
        margin: 10px 0;
    }
    .finding-low {
        background-color: #e3f2fd;
        border-left: 4px solid #2196f3;
        padding: 10px;
        border-radius: 5px;
        margin: 10px 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Get backend URL from environment or use default
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
logger.info("Streamlit frontend configured with BACKEND_URL=%s", BACKEND_URL)

# Initialize session state
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "current_step" not in st.session_state:
    st.session_state.current_step = "input"
if "analysis_data" not in st.session_state:
    st.session_state.analysis_data = {}
if "approval_submitted" not in st.session_state:
    st.session_state.approval_submitted = False
if "poll_count" not in st.session_state:
    st.session_state.poll_count = 0
if "poll_interval" not in st.session_state:
    st.session_state.poll_interval = 2
if "last_poll_status" not in st.session_state:
    st.session_state.last_poll_status = None
if "analysis_start_time" not in st.session_state:
    st.session_state.analysis_start_time = None


def _api_error_message(exc: Exception, response: "requests.Response | None" = None) -> str:
    """Return a user-friendly error string for a failed API call."""
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "Cannot connect to the analysis service. Please ensure the backend is running."
    if isinstance(exc, requests.exceptions.Timeout):
        return "The request timed out. The service may be busy — please try again."
    if isinstance(exc, requests.exceptions.HTTPError) and response is not None:
        status = response.status_code
        try:
            body = response.json()
            if status == 422:
                errors = body.get("detail", [])
                if isinstance(errors, list) and errors:
                    ctx_reason = (errors[0].get("ctx") or {}).get("reason", "")
                    msg = errors[0].get("msg", "")
                    return ctx_reason or msg or "Please enter a valid GitHub repository URL (e.g. https://github.com/owner/repo)."
                return "Please enter a valid GitHub repository URL (e.g. https://github.com/owner/repo)."
            detail = body.get("detail", "")
            if detail:
                return str(detail)
        except Exception:
            pass
        if status == 404:
            return "The requested analysis was not found. Please start a new analysis."
        if status >= 500:
            return "The analysis service encountered an internal error. Please try again."
        return f"Unexpected server response ({status}). Please try again."
    return "An unexpected error occurred. Please try again."


def get_status(thread_id: str) -> dict:
    """Check analysis status."""
    logger.info("Checking status for thread_id=%s", thread_id)
    response = None
    try:
        response = requests.get(f"{BACKEND_URL}/status/{thread_id}", timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error("Error checking status for thread_id=%s: %s", thread_id, e, exc_info=True)
        st.error(_api_error_message(e, response))
        return None


def submit_approval(thread_id: str, approved: bool) -> dict:
    """Submit approval decision."""
    logger.info("Submitting approval for thread_id=%s approved=%s", thread_id, approved)
    response = None
    try:
        response = requests.post(
            f"{BACKEND_URL}/approve",
            json={"thread_id": thread_id, "approved": approved},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error("Error submitting approval for thread_id=%s: %s", thread_id, e, exc_info=True)
        st.error(_api_error_message(e, response))
        return None


def start_analysis(repo_url: str) -> dict:
    """Start a new analysis."""
    logger.info("Starting analysis for repo_url=%s", repo_url)
    response = None
    try:
        response = requests.post(
            f"{BACKEND_URL}/analyze",
            json={"repo_url": repo_url},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error("Error starting analysis for repo_url=%s: %s", repo_url, e, exc_info=True)
        st.error(_api_error_message(e, response))
        return None


def display_findings(findings: list):
    """Display security findings with styling."""
    if not findings:
        st.info("No security vulnerabilities detected!")
        return

    # Group findings by severity
    severity_levels = {"critical": [], "high": [], "medium": [], "low": [], "info": []}

    for finding in findings:
        severity = finding.get("severity", "info").lower()
        if severity in severity_levels:
            severity_levels[severity].append(finding)
        else:
            severity_levels["info"].append(finding)

    # Display by severity
    severity_order = [
        ("critical", "🔴 Critical"),
        ("high", "🟠 High"),
        ("medium", "🟡 Medium"),
        ("low", "🟢 Low"),
        ("info", "ℹ️ Info"),
    ]

    for severity_key, severity_label in severity_order:
        findings_at_level = severity_levels[severity_key]
        if findings_at_level:
            st.subheader(f"{severity_label} ({len(findings_at_level)})")

            for idx, finding in enumerate(findings_at_level, 1):
                with st.expander(f"{idx}. {finding.get('title', 'Unknown Issue')}"):
                    safe_sev = html.escape(finding.get("severity", severity_key).upper())
                    st.markdown(
                        f'<div class="finding-{severity_key}"'
                        f' style="padding:6px 10px;margin-bottom:8px;">'
                        f'<strong>{safe_sev}</strong></div>',
                        unsafe_allow_html=True,
                    )
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**File:** `{finding.get('file', 'N/A')}`")
                        st.write(f"**Line:** {finding.get('line_reference', 'N/A')}")
                        st.write(f"**CWE:** {finding.get('cwe', 'N/A')}")

                    with col2:
                        st.write(f"**Type:** {finding.get('type', 'vulnerability')}")
                        st.write(f"**Severity:** {finding.get('severity', 'unknown')}")

                    st.write("**Description:**")
                    st.write(finding.get("description", "No description provided"))

                    st.write("**Recommendation:**")
                    st.write(finding.get("recommendation", "Please review manually"))


# Main UI
st.markdown(
    '<div class="main-header">'
    '<h1>🔍 Autonomous Codebase Librarian</h1>'
    '<p>Automated security analysis for GitHub repositories</p>'
    '</div>',
    unsafe_allow_html=True,
)

# Sidebar
with st.sidebar:
    st.title("About")
    st.info(
        """
        This tool automatically analyzes GitHub repositories for:
        - Security vulnerabilities in code
        - Vulnerable dependencies
        - Configuration issues

        All analysis requires human approval before final report generation.
        """
    )

    st.divider()

    if st.session_state.thread_id:
        if st.button("🔄 Start New Analysis"):
            st.session_state.thread_id = None
            st.session_state.current_step = "input"
            st.session_state.analysis_data = {}
            st.session_state.approval_submitted = False
            st.session_state.analysis_start_time = None
            st.rerun()

# Main content area
if st.session_state.current_step == "input":
    st.header("Repository Analysis")

    col1, col2 = st.columns([3, 1])
    with col1:
        repo_url = st.text_input(
            "GitHub Repository URL",
            placeholder="https://github.com/username/repository",
            help="Enter the full GitHub repository URL",
        )

    with col2:
        is_submitted = st.button("🚀 Analyze", use_container_width=True)

    if is_submitted and repo_url:
        with st.spinner("Starting analysis..."):
            result = start_analysis(repo_url)

            if result and "thread_id" in result:
                st.session_state.thread_id = result["thread_id"]
                st.session_state.poll_count = 0
                st.session_state.poll_interval = 2
                st.session_state.last_poll_status = None
                st.session_state.analysis_start_time = time.time()
                st.session_state.current_step = "progress"
                st.rerun()
            elif result:
                if result.get("status") == "error":
                    st.error(f"Analysis Error: {result.get('message', 'Unknown error')}")
                else:
                    st.error("Failed to start analysis")

elif st.session_state.current_step == "progress":
    st.header("Analysis Progress")

    status_data = get_status(st.session_state.thread_id)

    if status_data is None:
        st.error("Unable to reach the analysis service. Please refresh the page.")
    else:
        current_status = status_data.get("status", "unknown")

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Status", current_status.replace("_", " ").title())
        with col2:
            if status_data.get("findings_count"):
                st.metric("Total Findings", status_data["findings_count"].get("total", 0))
        with col3:
            if status_data.get("findings_count"):
                st.metric("Critical", status_data["findings_count"].get("critical", 0))
        with col4:
            if status_data.get("findings_count"):
                st.metric("High Priority", status_data["findings_count"].get("high", 0))

        st.info(status_data.get("message", "Processing..."))

        if current_status == "awaiting_approval":
            st.subheader("Security Findings Review")

            if status_data.get("findings"):
                display_findings(status_data["findings"])

            if not st.session_state.approval_submitted:
                approve_col, reject_col = st.columns(2)
                with approve_col:
                    if st.button("✅ Approve & Generate Report", use_container_width=True):
                        st.session_state.approval_submitted = True
                        with st.spinner("Generating report..."):
                            approval_result = submit_approval(st.session_state.thread_id, True)
                            if approval_result and approval_result.get("status") == "completed":
                                st.session_state.current_step = "report"
                                st.session_state.analysis_data = approval_result
                                st.rerun()
                            else:
                                st.session_state.approval_submitted = False
                                if approval_result and approval_result.get("status") == "error":
                                    st.error(f"Report generation failed: {approval_result.get('report', 'Unknown error')}")
                                else:
                                    st.error("Failed to generate the report. Please try again.")
                with reject_col:
                    if st.button("❌ Reject Analysis", use_container_width=True):
                        reject_result = submit_approval(st.session_state.thread_id, False)
                        if reject_result is None:
                            st.error("Failed to send rejection. Please try again.")
                            st.stop()
                        st.session_state.thread_id = None
                        st.session_state.current_step = "input"
                        st.session_state.approval_submitted = False
                        st.rerun()
            else:
                st.info("Processing approval...")

        elif current_status == "completed":
            st.session_state.current_step = "report"
            st.session_state.analysis_data = status_data
            st.rerun()

        elif current_status == "error":
            st.error(f"Analysis failed: {status_data.get('message', 'Unknown error')}")

        else:
            # Still in progress (scanning, analyzing) — show step progress and poll again
            step_map = {
                "scanning": (1, "Scanning repository structure"),
                "analyzing": (2, "Analyzing for vulnerabilities"),
            }
            step_num, step_label = step_map.get(current_status, (1, "Processing"))
            st.progress(step_num / 3, text=f"Step {step_num} of 3: {step_label}")

            elapsed = (
                int(time.time() - st.session_state.analysis_start_time)
                if st.session_state.analysis_start_time
                else 0
            )
            if elapsed >= 10:
                mins, secs = divmod(elapsed, 60)
                elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"
                st.caption(f"Elapsed: {elapsed_str} — Analysis typically takes 1–5 minutes.")
            else:
                st.caption("Analysis typically takes 1–5 minutes.")

            if current_status == st.session_state.last_poll_status:
                st.session_state.poll_interval = min(st.session_state.poll_interval * 1.5, 10)
            else:
                st.session_state.poll_interval = 2
            st.session_state.last_poll_status = current_status

            st.session_state.poll_count += 1
            if st.session_state.poll_count >= 300:
                st.error("Analysis timed out after 10 minutes. Please start a new analysis.")
            else:
                time.sleep(st.session_state.poll_interval)
                st.rerun()

elif st.session_state.current_step == "report":
    if st.session_state.analysis_data:
        report = st.session_state.analysis_data.get("report", "")
        findings = st.session_state.analysis_data.get("findings", [])

        # Display report
        st.markdown("## 📋 Final Security Analysis Report")

        # Download button
        col1, col2 = st.columns([4, 1])
        with col2:
            st.download_button(
                label="📥 Download Report",
                data=report,
                file_name=f"security_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
                mime="text/markdown",
            )

        # Display full report
        st.markdown(report)

        # Display detailed findings table
        if findings:
            st.subheader("Detailed Findings Table")

            findings_data = []
            for finding in findings:
                findings_data.append({
                    "Severity": finding.get("severity", "unknown"),
                    "Type": finding.get("type", "vulnerability"),
                    "Title": finding.get("title", ""),
                    "File": finding.get("file", ""),
                    "CWE": finding.get("cwe", "N/A"),
                })

            st.dataframe(findings_data, use_container_width=True)

        # Action buttons
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Start New Analysis", use_container_width=True):
                st.session_state.thread_id = None
                st.session_state.current_step = "input"
                st.session_state.analysis_data = {}
                st.session_state.approval_submitted = False
                st.session_state.analysis_start_time = None
                st.rerun()

        with col2:
            st.markdown("**✅ Analysis Complete**")

# Footer
st.divider()
st.caption("🚀 Autonomous Codebase Librarian v1.0")
