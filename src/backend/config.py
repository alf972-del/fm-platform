"""
config.py — Configuración y variables de entorno
=================================================
Usa pydantic-settings para validar y tipar todas las variables.
Carga desde .env en desarrollo, variables de sistema en producción.
"""

from pydantic_settings import BaseSettings
from pydantic import Field, PostgresDsn, RedisDsn
from typing import List, Optional
from functools import lru_cache


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────────
    APP_NAME: str = "FM Platform"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    PORT: int = 3000
    SECRET_KEY: str = Field(..., description="Clave secreta para JWT")
    
    # ── Database ─────────────────────────────────────
    DATABASE_URL: PostgresDsn = Field(
        default="postgresql+asyncpg://fm_user:password@localhost:5432/fm_platform",
        description="PostgreSQL async connection URL"
    )
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10
    
    # ── Redis ─────────────────────────────────────────
    REDIS_URL: RedisDsn = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL para caché y colas"
    )
    
    # ── Auth (Keycloak) ───────────────────────────────
    KEYCLOAK_URL: str = "http://localhost:8080"
    KEYCLOAK_REALM: str = "fm-platform"
    KEYCLOAK_CLIENT_ID: str = "fm-api"
    KEYCLOAK_CLIENT_SECRET: str = Field(..., description="Keycloak client secret")
    JWT_ALGORITHM: str = "RS256"
    JWT_PUBLIC_KEY: str = Field(..., description="Public key RSA de Keycloak para verificar JWT")
    
    # ── CORS ──────────────────────────────────────────
    CORS_ORIGINS: List[str] = [
        "http://localhost:5173",  # Vite dev
        "http://localhost:3001",  # Next.js dev
        "https://app.fmplatform.io",
        "https://portal.fmplatform.io",
    ]
    
    # ── S3 / MinIO ────────────────────────────────────
    S3_ENDPOINT: str = "https://s3.amazonaws.com"
    S3_BUCKET: str = "fm-platform-files"
    S3_ACCESS_KEY: str = Field(..., description="AWS/MinIO access key")
    S3_SECRET_KEY: str = Field(..., description="AWS/MinIO secret key")
    S3_REGION: str = "eu-west-1"
    S3_PRESIGNED_EXPIRY: int = 900  # 15 minutos
    
    # ── IoT / MQTT ────────────────────────────────────
    MQTT_BROKER_URL: str = "mqtt://localhost:1883"
    MQTT_USERNAME: Optional[str] = None
    MQTT_PASSWORD: Optional[str] = None
    MQTT_TOPIC_PREFIX: str = "fm/sensors"
    
    # ── Notificaciones ────────────────────────────────
    EXPO_PUSH_TOKEN: Optional[str] = None  # Para push notifications React Native
    SMTP_HOST: str = "smtp.sendgrid.net"
    SMTP_PORT: int = 587
    SMTP_USER: str = "apikey"
    SMTP_PASSWORD: Optional[str] = None
    EMAIL_FROM: str = "noreply@fmplatform.io"
    
    # ── Webhooks ──────────────────────────────────────
    WEBHOOK_SIGNING_SECRET: str = Field(..., description="HMAC-SHA256 secret para firmar webhooks")
    WEBHOOK_RETRY_MAX: int = 3
    WEBHOOK_RETRY_DELAYS: List[int] = [5, 25, 125]  # segundos
    
    # ── Caché ─────────────────────────────────────────
    CACHE_DEFAULT_TTL: int = 30        # segundos — dashboard
    CACHE_KPI_TTL: int = 60            # segundos — KPIs
    CACHE_ASSETS_TTL: int = 300        # segundos — lista activos
    
    # ── Rate Limiting ─────────────────────────────────
    RATE_LIMIT_STARTER: int = 100      # req/min
    RATE_LIMIT_GROWTH: int = 500
    RATE_LIMIT_SCALE: int = 2000
    RATE_LIMIT_SENSOR_INGEST: int = 10000
    
    # ── Meilisearch ───────────────────────────────────
    MEILISEARCH_URL: str = "http://localhost:7700"
    MEILISEARCH_API_KEY: str = Field(default="", description="Meilisearch admin key")
    
    # ── Sentry ────────────────────────────────────────
    SENTRY_DSN: Optional[str] = None
    SENTRY_ENVIRONMENT: str = "development"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Singleton de settings con caché."""
    return Settings()


settings = get_settings()
