#!/bin/bash

# Run script for Autonomous Codebase Librarian
# Starts both FastAPI backend and Streamlit frontend

set -e

echo "🚀 Starting Autonomous Codebase Librarian..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Creating from .env.example..."
    cp .env.example .env
    echo "📝 Please update .env with your GROQ_API_KEY before running again"
    exit 1
fi

# Check if Groq API key is set (basic validation)
if ! grep -q "^GROQ_API_KEY=" .env; then
    echo "❌ Error: GROQ_API_KEY not found in .env file"
    echo "📝 Please set your Groq API key: https://console.groq.com"
    exit 1
fi

# Create necessary directories
mkdir -p logs

echo "📦 Installing/updating dependencies..."
pip install -q -r requirements.txt

echo "🔄 Starting FastAPI backend on ${API_HOST}:${API_PORT}..."
python -m uvicorn backend.main:app \
    --host "${API_HOST}" \
    --port "${API_PORT}" \
    --log-level info \
    > logs/backend.log 2>&1 &

BACKEND_PID=$!
echo "   Backend PID: $BACKEND_PID"

# Wait for backend to start
sleep 3

# Check if backend started successfully
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "❌ Failed to start backend. Check logs/backend.log"
    exit 1
fi

echo "✅ Backend started successfully"

echo ""
echo "🎨 Starting Streamlit frontend on ${STREAMLIT_HOST}:${STREAMLIT_PORT}..."
echo "   Frontend URL: http://localhost:${STREAMLIT_PORT}"
echo ""

# Start Streamlit frontend (runs in foreground)
streamlit run frontend/app.py \
    --server.port="${STREAMLIT_PORT}" \
    --server.address="${STREAMLIT_HOST}" \
    --logger.level=info

# Cleanup
trap "kill $BACKEND_PID" EXIT
