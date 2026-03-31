import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Settings:
    MODEL_PROVIDER: str = os.getenv("MODEL_PROVIDER", "anthropic")

    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    MEMORY_TOP_K: int = int(os.getenv("MEMORY_TOP_K", "15"))
    MEMORY_TOKEN_BUDGET: int = int(os.getenv("MEMORY_TOKEN_BUDGET", "2000"))

    MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "10"))

    DATA_DIR: Path = Path(os.getenv("DATA_DIR", "data"))

    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://agent:agent_dev@localhost:5432/agent_chat",
    )

    QDRANT_HOST: str = os.getenv("QDRANT_HOST", "localhost")
    QDRANT_PORT: int = int(os.getenv("QDRANT_PORT", "6333"))

    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))

    @property
    def model_name(self) -> str:
        if self.MODEL_PROVIDER == "groq":
            return self.GROQ_MODEL
        return self.ANTHROPIC_MODEL


settings = Settings()
