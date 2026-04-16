"""FastAPI backend for the Autonomous Codebase Librarian."""

import logging
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.config import settings
from backend.graph import workflow, AnalysisState

logger = logging.getLogger(__name__)


# Request/Response models
class AnalyzeRequest(BaseModel):
    """Request model for starting analysis."""

    repo_url: str


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
        if not request.repo_url:
            logger.warning("Analyze request missing repo_url")
            raise HTTPException(status_code=400, detail="Repository URL is required")

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

        # Start the workflow (non-blocking, will hit interrupt)
        try:
            # Try to invoke the workflow
            workflow.invoke(initial_state, {"configurable": {"thread_id": thread_id}})
        except Exception as e:
            # Expected: interrupt will be raised
            if "interrupt" not in str(type(e).__name__).lower():
                # If it's not an interrupt, it might be an actual error
                if "error" in str(e).lower():
                    return AnalyzeResponse(
                        thread_id=thread_id,
                        status="error",
                        message=f"Analysis failed: {str(e)}",
                    )

        # If we got here without error, the interrupt paused us
        return AnalyzeResponse(
            thread_id=thread_id,
            status="awaiting_approval",
            message="Repository scanned and analyzed. Awaiting human approval.",
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

        # Determine status
        if state.get("analysis_report"):
            status = "completed"
            message = "Analysis complete and approved"
        elif state.get("security_findings"):
            status = "awaiting_approval"
            message = "Awaiting human approval of findings"
        elif state.get("file_structure"):
            status = "analyzing"
            message = "Security analysis in progress"
        elif state.get("error"):
            status = "error"
            message = state.get("error")
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

        # Update approval decision
        state["is_approved"] = request.approved

        if request.approved:
            logger.info(
                "Approval received for thread_id=%s: approved", request.thread_id
            )
            # Resume workflow to continue to compiler node
            try:
                workflow.invoke(
                    state, {"configurable": {"thread_id": request.thread_id}}
                )
            except Exception as e:
                if "interrupt" not in str(type(e).__name__).lower():
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
