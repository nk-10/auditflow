"""Streamlit frontend for the Autonomous Codebase Librarian."""

import logging
import streamlit as st
import requests
import time
from datetime import datetime
import os

logger = logging.getLogger(__name__)

# Page configuration
st.set_page_config(
    page_title="Autonomous Codebase Librarian",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
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


def get_status(thread_id: str) -> dict:
    """Check analysis status."""
    logger.info("Checking status for thread_id=%s", thread_id)
    try:
        response = requests.get(f"{BACKEND_URL}/status/{thread_id}", timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error("Error checking status for thread_id=%s: %s", thread_id, e, exc_info=True)
        st.error(f"Error checking status: {e}")
        return None


def submit_approval(thread_id: str, approved: bool) -> dict:
    """Submit approval decision."""
    logger.info("Submitting approval for thread_id=%s approved=%s", thread_id, approved)
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
        st.error(f"Error submitting approval: {e}")
        return None


def start_analysis(repo_url: str) -> dict:
    """Start a new analysis."""
    logger.info("Starting analysis for repo_url=%s", repo_url)
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
        st.error(f"Error starting analysis: {e}")
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

    # Display by severity
    severity_order = [("critical", "🔴 Critical"), ("high", "🟠 High"), ("medium", "🟡 Medium"), ("low", "🟢 Low"), ("info", "ℹ️ Info")]

    for severity_key, severity_label in severity_order:
        findings_at_level = severity_levels[severity_key]
        if findings_at_level:
            st.subheader(f"{severity_label} ({len(findings_at_level)})")

            for idx, finding in enumerate(findings_at_level, 1):
                with st.expander(f"{idx}. {finding.get('title', 'Unknown Issue')}"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**File:** `{finding.get('file', 'N/A')}`")
                        st.write(f"**Line:** {finding.get('line_reference', 'N/A')}")
                        st.write(f"**CWE:** {finding.get('cwe', 'N/A')}")

                    with col2:
                        st.write(f"**Type:** {finding.get('type', 'vulnerability')}")
                        st.write(f"**Severity:** {finding.get('severity', 'unknown')}")

                    st.write(f"**Description:**")
                    st.write(finding.get("description", "No description provided"))

                    st.write(f"**Recommendation:**")
                    st.write(finding.get("recommendation", "Please review manually"))


# Main UI
st.markdown(
    '<div class="main-header"><h1>🔍 Autonomous Codebase Librarian</h1><p>Automated security analysis for GitHub repositories</p></div>',
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
        st.write(f"**Current Analysis ID:**\n`{st.session_state.thread_id}`")
        if st.button("🔄 Start New Analysis"):
            st.session_state.thread_id = None
            st.session_state.current_step = "input"
            st.session_state.analysis_data = {}
            st.session_state.approval_submitted = False
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
                st.session_state.current_step = "progress"
                st.rerun()
            elif result:
                if result.get("status") == "error":
                    st.error(f"Analysis Error: {result.get('message', 'Unknown error')}")
                else:
                    st.error("Failed to start analysis")

elif st.session_state.current_step == "progress":
    st.header("Analysis Progress")

    # Poll for status
    status_placeholder = st.empty()
    with st.spinner("Checking analysis status..."):
        for i in range(300):  # Poll for up to 5 minutes
            status_data = get_status(st.session_state.thread_id)

            if status_data:
                current_status = status_data.get("status", "unknown")

                # Overwrite previous poll render in place
                with status_placeholder.container():
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("Status", current_status.replace("_", " ").title())

                    with col2:
                        if "findings_count" in status_data and status_data["findings_count"]:
                            st.metric("Total Findings", status_data["findings_count"].get("total", 0))

                    with col3:
                        if "findings_count" in status_data and status_data["findings_count"]:
                            st.metric("Critical", status_data["findings_count"].get("critical", 0))

                    with col4:
                        if "findings_count" in status_data and status_data["findings_count"]:
                            st.metric("High Priority", status_data["findings_count"].get("high", 0))

                    st.info(status_data.get("message", "Processing..."))

                # If awaiting approval, show findings and approval buttons
                if current_status == "awaiting_approval":
                    st.subheader("Security Findings Review")

                    if "findings" in status_data and status_data["findings"]:
                        display_findings(status_data["findings"])

                    col1, col2 = st.columns(2)

                    if not st.session_state.approval_submitted:
                        with col1:
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
                                        st.error(approval_result.get("report", "Failed to generate report") if approval_result else "Request failed")

                        with col2:
                            if st.button("❌ Reject Analysis", use_container_width=True):
                                st.session_state.approval_submitted = True
                                with st.spinner("Rejecting analysis..."):
                                    submit_approval(st.session_state.thread_id, False)
                                    st.session_state.thread_id = None
                                    st.session_state.current_step = "input"
                                    st.session_state.approval_submitted = False
                                    st.rerun()
                    else:
                        st.info("Processing approval...")

                    break

                # If completed, show report
                elif current_status == "completed":
                    st.session_state.current_step = "report"
                    st.session_state.analysis_data = status_data
                    st.rerun()

                # If error, show error
                elif current_status == "error":
                    st.error(f"Analysis failed: {status_data.get('message', 'Unknown error')}")
                    break

                elif current_status in ("scanning", "analyzing"):
                    pass  # expected in-progress states, keep polling

                else:
                    st.warning(f"Unexpected status '{current_status}'. Please refresh the page.")
                    break

            # Poll every 2 seconds
            time.sleep(2)

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
                st.rerun()

        with col2:
            st.markdown("**✅ Analysis Complete**")

# Footer
st.divider()
st.caption(f"🚀 Autonomous Codebase Librarian v1.0 | Backend: {BACKEND_URL}")
