"""Конфигурация приложения из переменных окружения (pydantic-settings).

Полный перечень переменных — docs/07-deployment.md#переменные-окружения.
Секреты читаются только отсюда; в код/логи/ответы API не попадают.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.logging import get_logger

logger = get_logger(__name__)

# Имена push-ботов почты (ADR-044 §9): bot_name ∈ этого набора.
MAIL_PUSH_BOT_NAMES: tuple[str, ...] = ("ivan", "alexandra", "andrei", "business2")


@dataclass(frozen=True, slots=True)
class MailPushBot:
    """Сконфигурированный push-бот команды (ADR-044 §9): токен + секрет + команда CRM."""

    name: str
    token: str
    webhook_secret: str
    team_id: uuid.UUID


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

    # --- Push-контракт агрегатор→CRM (ADR-044 §3, HMAC-SHA256) ---
    # Общий секрет HMAC push'а писем/статуса ящика (= агрегаторский CRM_PUSH_SECRET).
    # Класс секретов: только env, не в БД/логах/ответах/URL. Пусто → /api/mail/ingest и
    # /api/mail/mailbox-status выключены (503 mail_ingest_not_configured).
    mail_push_secret: str = ""
    # Окно валидности timestamp (сек): abs(now - ts) > skew → 401. Ограничивает поверхность,
    # анти-replay гасится идемпотентностью приёмника (ADR-044 §3).
    mail_push_max_skew_sec: int = 300
    # Потолок писем в одном батче POST /api/mail/ingest (> лимита → 400 validation_error).
    mail_ingest_max_batch: int = 100
    # TTL HMAC-подписанного crm_state для headless Outlook-OAuth (ADR-045 §3): покрывает
    # время прохождения consent в OctoBrowser. На /api/mail/oauth/ingest при `crm_state.exp`
    # в прошлом → 410 oauth_state_expired. Нормативно >= агрегаторского
    # OUTLOOK_OAUTH_STATE_TTL_SECONDS, чтобы consent, уложившийся в окно агрегатора,
    # проходил и в CRM. Секретом не является (подпись — через MAIL_PUSH_SECRET).
    mail_oauth_state_ttl_sec: int = 600

    @property
    def mail_ingest_enabled(self) -> bool:
        """Push-приёмник активен только при заданном MAIL_PUSH_SECRET (ADR-044 §3).

        Иначе /api/mail/ingest и /api/mail/mailbox-status → 503 mail_ingest_not_configured.
        """
        return bool(self.mail_push_secret)

    # --- Telegram-диспетчер почты (ADR-044 §6, S4) ---
    # Рубильник cut-over: диспетчер НЕ стартует, пока не включён (агрегатор глушится ДО
    # старта CRM-диспетчера, иначе двойная доставка). Default false.
    mail_dispatch_enabled: bool = False
    # Интервал итерации фоновой asyncio-задачи (проходы A/B/C).
    mail_dispatch_interval_sec: int = 5
    # Потолок писем/доставок/ящиков за один проход.
    mail_dispatch_batch: int = 100
    # Reconcile orphan-линков (safety-net §6) раз в N итераций (~ N*interval секунд).
    mail_dispatch_reconcile_every: int = 12
    # Потолок попыток доставки одного уведомления (проход B, attempts >= max → dead).
    mail_tg_max_attempts: int = 6
    # Уведомлять обо ВСЕХ письмах (паритет TG_NOTIFY_ALL_MESSAGES); false → только с ≥1 тегом.
    mail_tg_notify_all_messages: bool = True
    # TTL initData Mini App `/tg/mail` (по `auth_date`), сек.
    mail_tg_initdata_ttl_sec: int = 300

    # --- Основной бот @ba_mail_bot (ADR-044 §6/§9) ---
    # Токен основного бота (webhook + Mini App SSO). Пусто → mail_bot_enabled=false.
    mail_bot_token: str = ""
    # Секрет webhook основного бота (URL-сегмент + X-Telegram-Bot-Api-Secret-Token).
    mail_bot_webhook_secret: str = ""
    # URL Mini App `/tg/mail` (кнопка web_app в приветствии /start).
    mail_bot_webapp_url: str = ""
    # Опциональный egress-прокси ботов почты к api.telegram.org. Пусто → прямой.
    mail_bot_proxy_url: str = ""

    # --- 4 push-бота по командам (ADR-044 §9): токен/секрет/team_id (UUID CRM) ---
    mail_bot_ivan_token: str = ""
    mail_bot_ivan_webhook_secret: str = ""
    mail_bot_ivan_team_id: str = ""
    mail_bot_alexandra_token: str = ""
    mail_bot_alexandra_webhook_secret: str = ""
    mail_bot_alexandra_team_id: str = ""
    mail_bot_andrei_token: str = ""
    mail_bot_andrei_webhook_secret: str = ""
    mail_bot_andrei_team_id: str = ""
    mail_bot_business2_token: str = ""
    mail_bot_business2_webhook_secret: str = ""
    mail_bot_business2_team_id: str = ""
    # CSV Telegram-id админов для push-fan-out (ADR-044 §6 шаг 3 / §9).
    mail_admin_telegram_ids: str = ""

    @property
    def mail_push_bots(self) -> list[MailPushBot]:
        """Сконфигурированные push-боты почты (ADR-044 §9). Fail-fast — в model_post_init."""
        return self._build_mail_push_bots()

    @property
    def mail_bot_enabled(self) -> bool:
        """Основной почтовый бот активен только при заданном MAIL_BOT_TOKEN (ADR-044 §9)."""
        return bool(self.mail_bot_token)

    @property
    def mail_admin_telegram_ids_list(self) -> list[int]:
        """Список Telegram-id админов (CSV MAIL_ADMIN_TELEGRAM_IDS); мусор пропускается."""
        result: list[int] = []
        for raw in self.mail_admin_telegram_ids.split(","):
            token = raw.strip()
            if not token:
                continue
            try:
                result.append(int(token))
            except ValueError:
                logger.warning("mail_admin_telegram_id_invalid")
        return result

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
        # Fail-fast на старте: невалидный/дублирующийся MAIL_BOT_*_TEAM_ID → ValueError.
        self._build_mail_push_bots()

    def _build_mail_push_bots(self) -> list[MailPushBot]:
        """Собрать сконфигурированные push-боты почты (ADR-044 §9), fail-fast на старте.

        Бот «сконфигурирован» ⇔ заданы токен И секрет И team_id. Невалидный UUID team_id
        → ValueError (fail-fast). Дубликат team_id среди ботов → ValueError (fail-fast,
        §9): один и тот же ящик не может уходить в две команды.
        """
        raw_bots = [(name, self._push_bot_env(name)) for name in MAIL_PUSH_BOT_NAMES]
        bots: list[MailPushBot] = []
        seen_team_ids: set[uuid.UUID] = set()
        for name, (token, secret, team_id_raw) in raw_bots:
            if not (token and secret and team_id_raw):
                continue
            try:
                team_id = uuid.UUID(team_id_raw)
            except ValueError as exc:
                raise ValueError(
                    f"MAIL_BOT_{name.upper()}_TEAM_ID не является валидным UUID"
                ) from exc
            if team_id in seen_team_ids:
                raise ValueError(
                    f"Дубликат MAIL_BOT_*_TEAM_ID: {team_id} привязан к нескольким ботам"
                )
            seen_team_ids.add(team_id)
            bots.append(MailPushBot(name=name, token=token, webhook_secret=secret, team_id=team_id))
        return bots

    def _push_bot_env(self, name: str) -> tuple[str, str, str]:
        """Тройка (token, webhook_secret, team_id_raw) для push-бота по имени."""
        mapping: dict[str, tuple[str, str, str]] = {
            "ivan": (
                self.mail_bot_ivan_token,
                self.mail_bot_ivan_webhook_secret,
                self.mail_bot_ivan_team_id,
            ),
            "alexandra": (
                self.mail_bot_alexandra_token,
                self.mail_bot_alexandra_webhook_secret,
                self.mail_bot_alexandra_team_id,
            ),
            "andrei": (
                self.mail_bot_andrei_token,
                self.mail_bot_andrei_webhook_secret,
                self.mail_bot_andrei_team_id,
            ),
            "business2": (
                self.mail_bot_business2_token,
                self.mail_bot_business2_webhook_secret,
                self.mail_bot_business2_team_id,
            ),
        }
        return mapping[name]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Кешированный синглтон настроек."""
    return Settings()
