@echo off
REM Run script for Autonomous Codebase Librarian (Windows)
REM Starts both FastAPI backend and Streamlit frontend

echo.
echo 🚀 Starting Autonomous Codebase Librarian...
echo.

REM Check if .env file exists
if not exist .env (
    echo ⚠️  .env file not found. Creating from .env.example...
    copy .env.example .env
    echo 📝 Please update .env with your GROQ_API_KEY before running again
    pause
    exit /b 1
)

REM Create necessary directories
if not exist logs mkdir logs

echo 📦 Installing/updating dependencies...
pip install -q -r requirements.txt

echo.
echo 🔄 Starting FastAPI backend...
start "AuditFlow Backend" cmd /k "python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --log-level info"

REM Wait for backend to start
timeout /t 3 /nobreak

echo.
echo 🎨 Starting Streamlit frontend...
echo    Frontend URL: http://localhost:8501
echo.

REM Start Streamlit (blocking)
streamlit run frontend/app.py --server.port=8501 --server.address=0.0.0.0 --logger.level=info

pause
