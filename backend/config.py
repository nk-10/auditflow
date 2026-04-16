import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Groq API
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")

    # GitHub
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    max_files_to_analyze: int = 100
    max_file_size_mb: int = 1  # Skip files larger than 1MB

    # API Server
    api_host: str = os.getenv("API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("API_PORT", "8000"))

    # Streamlit Frontend
    streamlit_host: str = os.getenv("STREAMLIT_HOST", "127.0.0.1")
    streamlit_port: int = int(os.getenv("STREAMLIT_PORT", "8501"))
    backend_url: str = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")

    # LangGraph
    db_path: str = os.getenv("DB_PATH", "./data/checkpoints.db")

    # LLM Configuration
    llm_model: str = "llama-3.3-70b-versatile"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4000

    # Feature flags
    enable_cors: bool = True
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"

    class Config:
        env_file = ".env"


settings = Settings()
