"""FastAPI backend for the Autonomous Codebase Librarian."""

import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langgraph.errors import GraphInterrupt
from langgraph.types import Command
from pydantic import BaseModel, field_validator

from backend.config import settings
from backend.graph import workflow, AnalysisState

logger = logging.getLogger(__name__)


# Request/Response models
class AnalyzeRequest(BaseModel):
    """Request model for starting analysis."""

    repo_url: str

    @field_validator("repo_url")
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        """Validate that the URL is a well-formed public GitHub repository URL."""
        v = v.strip().rstrip("/")
        if not re.match(r"^https://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$", v):
            raise ValueError(
                "Must be a valid public GitHub repository URL "
                "(e.g. https://github.com/owner/repo)"
            )
        return v


class ApprovalRequest(BaseModel):
    """Request model for approval decision."""

    thread_id: str
    approved: bool


class AnalyzeResponse(BaseModel):
    """Response model for analysis initiation."""

    thread_id: str
    status: str
    message: str


class StatusResponse(BaseModel):
    """Response model for status check."""

    thread_id: str
    status: str
    message: str
    findings_count: dict = None
    findings: list = None


class ApprovalResponse(BaseModel):
    """Response model for approval decision."""

    thread_id: str
    status: str
    report: str = ""
    findings: list = []


# Health check counter
health_check_count = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    print("🚀 Autonomous Codebase Librarian Backend started")
    print(f"   API running at http://{settings.api_host}:{settings.api_port}")
    print(f"   Groq Model: {settings.llm_model}")
    yield
    print("🛑 Backend shutting down")


# Initialize FastAPI app
app = FastAPI(
    title="Autonomous Codebase Librarian",
    description="Security analysis system for GitHub repositories with human-in-the-loop approval",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware if enabled
if settings.enable_cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    global health_check_count
    health_check_count += 1
    return {"status": "healthy", "checks": health_check_count}


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    """Start a new security analysis for a GitHub repository.

    Args:
        request: Request containing repository URL

    Returns:
        Response with thread_id and initial status
    """
    try:
        logger.info("Starting analysis for repo_url=%s", request.repo_url)
        # Generate unique thread ID
        thread_id = str(uuid.uuid4())

        # Initialize state
        initial_state: AnalysisState = {
            "repo_url": request.repo_url,
            "file_structure": None,
            "security_findings": [],
            "analysis_report": "",
            "is_approved": False,
            "thread_id": thread_id,
            "error": None,
        }

        async def run_workflow():
            try:
                await asyncio.to_thread(
                    workflow.invoke,
                    initial_state,
                    {"configurable": {"thread_id": thread_id}},
                )
            except GraphInterrupt:
                # Expected: workflow paused at human_review interrupt
                pass
            except Exception as e:
                logger.error(
                    "Background workflow error for thread_id=%s: %s",
                    thread_id,
                    e,
                    exc_info=True,
                )

        asyncio.create_task(run_workflow())

        return AnalyzeResponse(
            thread_id=thread_id,
            status="scanning",
            message="Analysis started.",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error starting analysis for repo_url=%s: %s",
            request.repo_url,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail=f"Error starting analysis: {str(e)}"
        )


@app.get("/status/{thread_id}", response_model=StatusResponse)
async def check_status(thread_id: str) -> StatusResponse:
    """Check the status of an ongoing or completed analysis.

    Args:
        thread_id: Unique identifier for the analysis thread

    Returns:
        Current status and findings
    """
    try:
        # Get current state from the thread
        state_snapshot = workflow.get_state({"configurable": {"thread_id": thread_id}})

        if not state_snapshot:
            logger.warning("Status request for missing thread_id=%s", thread_id)
            raise HTTPException(status_code=404, detail="Thread not found")

        state = state_snapshot.values

        findings = state.get("security_findings", [])
        findings_count = {
            "critical": sum(1 for f in findings if f.get("severity") == "critical"),
            "high": sum(1 for f in findings if f.get("severity") == "high"),
            "medium": sum(1 for f in findings if f.get("severity") == "medium"),
            "low": sum(1 for f in findings if f.get("severity") == "low"),
            "total": len(findings),
        }

        # Determine status.
        # IMPORTANT: check error before file_structure — a failed security_node leaves
        # file_structure populated but security_findings empty, which would otherwise
        # return "analyzing" forever and cause the frontend to poll indefinitely.
        if state.get("analysis_report"):
            status = "completed"
            message = "Analysis complete and approved"
        elif state.get("security_findings"):
            status = "awaiting_approval"
            message = "Awaiting human approval of findings"
        elif state.get("error"):
            status = "error"
            message = state.get("error")
        elif state.get("file_structure"):
            status = "analyzing"
            message = "Security analysis in progress"
        else:
            status = "scanning"
            message = "Scanning repository structure"

        return StatusResponse(
            thread_id=thread_id,
            status=status,
            message=message,
            findings_count=findings_count,
            findings=findings,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error checking status for thread_id=%s: %s", thread_id, e, exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Error checking status: {str(e)}")


@app.post("/approve", response_model=ApprovalResponse)
async def submit_approval(request: ApprovalRequest) -> ApprovalResponse:
    """Submit approval decision for security findings.

    Args:
        request: Request with thread_id and approval decision

    Returns:
        Response with generated report if approved
    """
    try:
        # Get current state
        state_snapshot = workflow.get_state(
            {"configurable": {"thread_id": request.thread_id}}
        )

        if not state_snapshot:
            logger.warning(
                "Approval request for missing thread_id=%s", request.thread_id
            )
            raise HTTPException(status_code=404, detail="Thread not found")

        state = state_snapshot.values

        if request.approved:
            logger.info(
                "Approval received for thread_id=%s: approved", request.thread_id
            )
            # Resume workflow from the human_review interrupt checkpoint
            try:
                await asyncio.to_thread(
                    workflow.invoke,
                    Command(resume={"is_approved": True}),
                    {"configurable": {"thread_id": request.thread_id}},
                )
            except GraphInterrupt:
                # Not expected here, but handle defensively
                pass
            except Exception as e:
                logger.error(
                    "Error resuming workflow for thread_id=%s: %s",
                    request.thread_id,
                    e,
                    exc_info=True,
                )
            # Get updated state with report
            final_state_snapshot = workflow.get_state(
                {"configurable": {"thread_id": request.thread_id}}
            )
            final_state = final_state_snapshot.values if final_state_snapshot else state

            if final_state.get("error"):
                return ApprovalResponse(
                    thread_id=request.thread_id,
                    status="error",
                    report=f"Report generation failed: {final_state.get('error')}",
                    findings=final_state.get("security_findings", []),
                )

            return ApprovalResponse(
                thread_id=request.thread_id,
                status="completed",
                report=final_state.get("analysis_report", ""),
                findings=final_state.get("security_findings", []),
            )
        else:
            logger.info(
                "Approval received for thread_id=%s: rejected", request.thread_id
            )
            return ApprovalResponse(
                thread_id=request.thread_id,
                status="rejected",
                report="Analysis rejected by user. No report generated.",
                findings=state.get("security_findings", []),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error processing approval for thread_id=%s: %s",
            request.thread_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail=f"Error processing approval: {str(e)}"
        )


@app.get("/", tags=["root"])
async def root() -> dict:
    """Root endpoint providing API information."""
    return {
        "name": "Autonomous Codebase Librarian API",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "analyze": "/analyze (POST)",
            "status": "/status/{thread_id} (GET)",
            "approve": "/approve (POST)",
            "docs": "/docs",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="info" if not settings.debug else "debug",
    )
