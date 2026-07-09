"""Конфигурация приложения из переменных окружения (pydantic-settings).

Полный перечень переменных — docs/07-deployment.md#переменные-окружения.
Секреты читаются только отсюда; в код/логи/ответы API не попадают.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.logging import get_logger

logger = get_logger(__name__)


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
    jwt_expires_min: int = 1440
    jwt_algorithm: str = "HS256"
    # TTL limited-scope setup-токена «первого входа» (type:"pwd_setup", ADR-025).
    # Выдаётся беспарольному пользователю; принимается только POST /api/auth/set-password.
    pwd_setup_token_expires_min: int = 10

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
    # Окно max_over_time для оценки зоны CPU/RAM/SSD нотификатором (ADR-016).
    # Нормативно >= notifier_poll_interval_sec; при задании меньше — эффективное
    # окно поднимается до poll_interval с warning-логом (см. model_post_init).
    # UI/read-path не затрагивает (window_sec=None), окно применяется только у бота.
    notifier_metric_window_sec: int = 90
    # Эффективное окно после клампа к poll_interval; используется нотификатором.
    notifier_metric_window_effective_sec: int = Field(default=0, exclude=True)

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

    # --- Монитор доступности прокси (modules/proxies, ADR-019) ---
    # Интервал периодической проверки всех прокси; монитор стартует всегда.
    proxy_check_interval_sec: int = 60
    # Таймаут HTTP-запроса через прокси к эталонному URL (per-attempt, все фазы httpx).
    proxy_check_timeout_sec: float = 10.0
    # Overall-deadline проверки одного прокси (анти-зависание, asyncio.wait_for; ADR-024).
    # Превышение → error «Таймаут подключения». deadline (30 с) < интервал (60 с).
    proxy_check_deadline_sec: float = 30.0
    # Эталонный URL проверки связности через прокси (лёгкий 204 No Content).
    proxy_check_url: str = "https://www.gstatic.com/generate_204"
    # Grace-порог: непрерывная недоступность прокси (сек) перед 🔴-алертом (30 мин, ADR-027).
    # check_status→error немедленно; откладывается только уведомление (error_since/alert_sent).
    proxy_alert_after_sec: int = 1800

    # --- Монитор доступности бэков (modules/backends, ADR-020) ---
    # Интервал периодической проверки всех бэков; монитор стартует всегда.
    backend_check_interval_sec: int = 60
    # Таймаут HTTP-запроса GET https://{domain}/health (per-attempt, все фазы httpx).
    backend_check_timeout_sec: float = 10.0
    # Overall-deadline проверки одного бэка (анти-зависание, asyncio.wait_for; ADR-024).
    backend_check_deadline_sec: float = 30.0
    # Grace-порог: непрерывная недоступность бэка (сек) перед 🔴-алертом (30 мин, ADR-024).
    # check_status→error немедленно; откладывается только уведомление (error_since/alert_sent).
    backend_alert_after_sec: int = 1800

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

    # --- Модуль «СМС» (Twilio-приём + Telegram-доставка, modules/sms, ADR-030) ---
    # Twilio: креды для валидации подписи webhook и Numbers API (sync). Секрет —
    # только env, не в БД/логах/ответах/URL (05-security.md#защита-модуля-смс).
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    # Проверять X-Twilio-Signature на POST /api/sms/webhooks/twilio/sms. Прод — true;
    # true без TWILIO_AUTH_TOKEN → 503 twilio_not_configured. Отключать только локально.
    verify_twilio_signature: bool = True
    # Публичный базовый URL CRM — ЕДИНСТВЕННЫЙ источник реконструкции URL при проверке
    # подписи Twilio (05-security.md#подпись-twilio). X-Forwarded-* для подписи не берётся.
    sms_public_base_url: str = ""
    # Отдельный SMS-delivery Telegram-бот (НЕ notifier-бот). Пусто → sms_bot_enabled=false
    # (доставка/retry-монитор не стартуют; приём SMS работает и сохраняет без доставки).
    sms_telegram_bot_token: str = ""
    # Секрет-токен Telegram-webhook SMS-бота (X-Telegram-Bot-Api-Secret-Token, constant-time).
    sms_telegram_webhook_secret: str = ""
    # URL Mini App (кнопка web_app в приветствии /start).
    sms_telegram_webapp_url: str = ""
    # Опциональный прокси egress SMS-бота к api.telegram.org (socks5://…/http://…). Пусто → прямой.
    sms_telegram_proxy_url: str = ""
    # Интервал фонового retry-монитора доставок SMS (сек). Стартует при sms_bot_enabled.
    sms_delivery_retry_interval_sec: int = 60
    # Потолок попыток доставки одного SMS одному получателю до остановки (retry-монитор).
    sms_delivery_max_attempts: int = 5

    @property
    def sms_bot_enabled(self) -> bool:
        """SMS-бот активен только при заданном SMS_TELEGRAM_BOT_TOKEN (ADR-030).

        Иначе доставка операторам и retry-монитор не стартуют (приём SMS сохраняет
        входящие без Telegram-доставки).
        """
        return bool(self.sms_telegram_bot_token)

    @property
    def twilio_configured(self) -> bool:
        """Twilio настроен только при заданных ACCOUNT_SID И AUTH_TOKEN (ADR-030).

        Иначе POST /api/sms/numbers/sync → 503 twilio_not_configured.
        """
        return bool(self.twilio_account_sid and self.twilio_auth_token)

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
        # Клампуем окно нотификатора к poll_interval (ADR-016): окно < интервала
        # опроса оставляет «слепые» зазоры между окнами → дефект не устраняется.
        effective_window = self.notifier_metric_window_sec
        if effective_window < self.notifier_poll_interval_sec:
            logger.warning(
                "notifier_metric_window_clamped",
                configured_sec=self.notifier_metric_window_sec,
                poll_interval_sec=self.notifier_poll_interval_sec,
                effective_sec=self.notifier_poll_interval_sec,
            )
            effective_window = self.notifier_poll_interval_sec
        object.__setattr__(self, "notifier_metric_window_effective_sec", effective_window)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Кешированный синглтон настроек."""
    return Settings()
