"""
app/core/config.py — Configuración centralizada con Pydantic Settings
"""

from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    VERSION: str = "1.0.0"
    APP_NAME: str = "FM Platform"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://fm:fm@localhost:5432/fmplatform"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_TTL_SECONDS: int = 30  # Dashboard cache

    # Auth (Keycloak OIDC)
    KEYCLOAK_URL: str = "http://localhost:8080"
    KEYCLOAK_REALM: str = "fmplatform"
    KEYCLOAK_CLIENT_ID: str = "fm-api"
    KEYCLOAK_CLIENT_SECRET: str = "change-me"

    # S3 / MinIO
    S3_ENDPOINT: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "fm-attachments"
    S3_PRESIGN_EXPIRY: int = 900  # 15 min

    # CORS
    CORS_ORIGINS: List[str] = [
        "http://localhost:5173",   # Vite dev
        "http://localhost:3001",   # Next.js portal inquilinos
        "https://app.fmplatform.io",
        "https://portal.fmplatform.io",
    ]

    # Rate limiting
    RATE_LIMIT_DEFAULT: int = 100   # req/min — Starter
    RATE_LIMIT_GROWTH: int = 500
    RATE_LIMIT_SCALE: int = 2000
    RATE_LIMIT_SENSOR: int = 10000  # Ingestión IoT

    # Meilisearch
    MEILISEARCH_URL: str = "http://localhost:7700"
    MEILISEARCH_KEY: str = "masterKey"

    # MQTT (IoT)
    MQTT_BROKER: str = "localhost"
    MQTT_PORT: int = 1883
    MQTT_USERNAME: str = ""
    MQTT_PASSWORD: str = ""

    # Email (notifications)
    SMTP_HOST: str = "smtp.sendgrid.net"
    SMTP_PORT: int = 587
    SMTP_USER: str = "apikey"
    SMTP_PASS: str = ""
    EMAIL_FROM: str = "noreply@fmplatform.io"

    # Expo Push (mobile)
    EXPO_PUSH_URL: str = "https://exp.host/--/api/v2/push/send"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
