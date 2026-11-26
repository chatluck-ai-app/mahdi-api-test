import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./mahdi_local.db")
if DATABASE_URL.startswith(("postgres://", "postgresql")):
    # psycopg2 is the installed driver; normalize any postgres URL form to it
    DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL.split("://", 1)[1]

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")

PROVIDER_PRIORITY = [
    p.strip()
    for p in os.getenv("AI_PROVIDER_PRIORITY", "gemini,openai,anthropic").split(",")
    if p.strip()
]

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

CODE_RUN_TIMEOUT = int(os.getenv("CODE_RUN_TIMEOUT", "10"))
CODE_RUN_MAX_OUTPUT = int(os.getenv("CODE_RUN_MAX_OUTPUT", str(64 * 1024)))
CODE_RUN_MAX_MEMORY_MB = int(os.getenv("CODE_RUN_MAX_MEMORY_MB", "256"))
