"""SQLAlchemy-модели."""

from app.models.ai_key import AiKey, AiKeyStatus, AiProvider
from app.models.base import Base
from app.models.document_attachment import DocumentAttachment
from app.models.document_node import DocumentNode
from app.models.document_node_role import document_node_roles
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
from app.models.server import ProvisionStatus, Server, ServerAuthMethod
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
    "DocumentAttachment",
    "DocumentNode",
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
    "ServerAuthMethod",
    "SmsDelivery",
    "SmsInbound",
    "SmsPhoneNumber",
    "SmsTelegramLink",
    "Team",
    "User",
    "document_node_roles",
    "user_channel_teams",
    "user_teams",
]
