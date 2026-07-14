"""SQLAlchemy-модели."""

from app.models.ai_key import AiKey, AiKeyStatus, AiProvider
from app.models.base import Base
from app.models.mail_account import MailAccount
from app.models.mail_message import MailMessage
from app.models.mail_message_read import MailMessageRead
from app.models.mail_sent_message import MailSentMessage
from app.models.mail_tag import MailMessageTag, MailTag, MailTagRule
from app.models.mail_telegram import MailTelegramLink, MailTelegramNotification
from app.models.mail_user_settings import MailUserSettings
from app.models.notifier_alert_log import NotifierAlertLog
from app.models.notifier_server_state import NotifierServerState
from app.models.proxy import Proxy, ProxyStatus, ProxyType
from app.models.role import Role
from app.models.server import ProvisionStatus, Server
from app.models.service_backend import Backend, BackendStatus
from app.models.sms_delivery import SmsDelivery
from app.models.sms_inbound import SmsInbound
from app.models.sms_phone_number import SmsPhoneNumber
from app.models.sms_telegram_link import SmsTelegramLink
from app.models.team import Team, user_teams
from app.models.user import User
from app.models.user_channel_team import user_channel_teams

__all__ = [
    "AiKey",
    "AiKeyStatus",
    "AiProvider",
    "Backend",
    "BackendStatus",
    "Base",
    "MailAccount",
    "MailMessage",
    "MailMessageRead",
    "MailMessageTag",
    "MailSentMessage",
    "MailTag",
    "MailTagRule",
    "MailTelegramLink",
    "MailTelegramNotification",
    "MailUserSettings",
    "NotifierAlertLog",
    "NotifierServerState",
    "ProvisionStatus",
    "Proxy",
    "ProxyStatus",
    "ProxyType",
    "Role",
    "Server",
    "SmsDelivery",
    "SmsInbound",
    "SmsPhoneNumber",
    "SmsTelegramLink",
    "Team",
    "User",
    "user_channel_teams",
    "user_teams",
]
