"""Конфигурация приложения из переменных окружения (pydantic-settings).

Полный перечень переменных — docs/07-deployment.md#переменные-окружения.
Секреты читаются только отсюда; в код/логи/ответы API не попадают.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(str, Enum):
    """Окружение приложения. На production отключается /api/docs (см. 05-security.md)."""

    development = "development"
    production = "production"


class Settings(BaseSettings):
    """Настройки backend. Значения по умолчанию — из docs/02-tech-stack.md и 07-deployment.md."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnv = AppEnv.development

    # --- Аутентификация администратора (ADR-008, 05-security.md) ---
    admin_user: str = "admin"
    admin_password: str = "change-me"

    # --- JWT (05-security.md) ---
    jwt_secret: str = "change-me-please-use-32-bytes-min-secret"
    jwt_expires_min: int = 60
    jwt_algorithm: str = "HS256"

    # --- Шифрование SSH-паролей (Fernet, ADR-007) ---
    fernet_key: str = ""

    # --- База данных ---
    database_url: str = "postgresql+asyncpg://crm:pwd@postgres:5432/crm"

    # --- Prometheus (monitoring) ---
    prometheus_url: str = "http://prometheus:9090"
    prom_query_timeout_sec: float = 10.0
    # TTL кэша результатов метрик (short-lived, гасит thundering herd на read-path).
    metrics_cache_ttl_sec: float = 5.0

    # --- Telegram-нотификатор (modules/notifier, ADR-009) ---
    # Активен только если заданы обе переменные (непустые); иначе задача не стартует.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    notifier_poll_interval_sec: int = 60

    @property
    def notifier_enabled(self) -> bool:
        """Нотификатор активен только при заданных токене И chat_id (modules/notifier)."""
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    # --- Монитор AI-ключей (modules/ai-keys, ADR-010) ---
    # Интервал периодической проверки всех ключей; монитор стартует всегда.
    ai_key_check_interval_sec: int = 900
    # Таймаут HTTP-запроса к провайдеру при проверке ключа (GET /v1/models).
    ai_provider_timeout_sec: float = 10.0
    openai_api_base: str = "https://api.openai.com/v1"
    anthropic_api_base: str = "https://api.anthropic.com/v1"
    anthropic_api_version: str = "2023-06-01"

    # --- Модуль «Почты» (read-through-прокси, modules/mail, ADR-012) ---
    # Backend проксирует /api/mail/* во внешний сервис postapp.store, подставляя
    # MAIL_API_KEY в заголовок X-API-Key. Ключ — только из env, не в БД/логах/ответах.
    mail_api_base: str = "https://postapp.store"
    mail_api_key: str = ""
    mail_api_timeout_sec: int = 10

    @property
    def mail_enabled(self) -> bool:
        """Почта активна только при заданном MAIL_API_KEY (modules/mail).

        Иначе оба эндпоинта /api/mail/* → 503 mail_not_configured.
        """
        return bool(self.mail_api_key)

    # --- Провижининг / node_exporter ---
    exporter_port: int = 9100
    file_sd_dir: str = "/etc/prometheus/targets"
    ansible_timeout_sec: int = 300
    ansible_host_key_checking: bool = False
    ansible_playbook_path: str = "ansible/install_node_exporter.yml"
    # Публичный IP CRM-сервера, с которого Prometheus скрейпит цели; плейбук
    # открывает firewall на цели для этого источника (TD-017). Пусто → шаг
    # firewall пропускается.
    scrape_source_ip: str = ""

    # --- node_exporter (02-tech-stack.md) ---
    node_exporter_version: str = "1.8.2"
    node_exporter_url: str = (
        "https://github.com/prometheus/node_exporter/releases/download/"
        "v1.8.2/node_exporter-1.8.2.linux-amd64.tar.gz"
    )
    node_exporter_sha256: str = "6809dd0b3ec45fd6e992c19071d6b5253aed3ead7bf0686885a51d85c6643c66"

    # --- Rate-limit входа (05-security.md, TD-005) ---
    login_rate_limit_attempts: int = 10
    login_rate_limit_window_sec: int = 300

    # --- CORS ---
    cors_allow_origins: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        """Список разрешённых origin (CSV в CORS_ALLOW_ORIGINS)."""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def docs_enabled(self) -> bool:
        """В production интерактивная документация и OpenAPI отключены (05-security.md)."""
        return self.app_env is AppEnv.development

    jwt_expires_seconds: int = Field(default=0, exclude=True)

    def model_post_init(self, __context: object) -> None:
        object.__setattr__(self, "jwt_expires_seconds", self.jwt_expires_min * 60)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Кешированный синглтон настроек."""
    return Settings()
