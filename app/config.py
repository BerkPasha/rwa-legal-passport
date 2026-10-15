from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "RWA Legal Passport"
    app_version: str = "0.1.0"
    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./rwa_legal_passport.db"
    max_upload_size_mb: int = 10
    storage_path: str = "./storage"
    # AI servisi — gerçek kullanım için .env dosyasına ekleyin
    anthropic_api_key: str = ""
    ai_model: str = "claude-haiku-4-5-20251001"
    # Kimlik bilgileri — yalnızca .env dosyasından okunur, kaynak kodda tutulmaz.
    # Boş bırakılan token/anahtar devre dışıdır ve hiçbir isteği doğrulamaz.
    reviewer_issuer_token: str = ""
    reviewer_legal_token: str = ""
    institutional_api_key_alpha: str = ""
    institutional_api_key_beta: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
