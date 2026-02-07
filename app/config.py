from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "dev"
    APP_ORIGIN: str = "http://localhost:5173"

    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_JWT_SECRET: str

    GEMINI_API_KEY: str
    GEMINI_MODEL_CHAT: str = "gemini-2.0-flash"
    GEMINI_MODEL_EVAL: str = "gemini-2.0-flash"

    HISTORY_TURNS: int = 30

settings = Settings()
