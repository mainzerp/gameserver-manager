import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def send_email(self, subject: str, body: str, html_body: str | None = None):
        if not settings.smtp_enabled or not settings.smtp_host:
            return
        try:
            import aiosmtplib
        except ImportError:
            logger.warning("aiosmtplib not installed, email notifications disabled")
            return

        recipients = [
            a.strip() for a in settings.smtp_to_addresses.split(",") if a.strip()
        ]
        if not recipients:
            logger.warning("No SMTP recipients configured")
            return

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = settings.smtp_from_address or settings.smtp_user
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))
            if html_body:
                msg.attach(MIMEText(html_body, "html"))

            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user or None,
                password=settings.smtp_password or None,
                use_tls=settings.smtp_use_tls,
            )
            logger.info(f"Email sent: {subject}")
        except Exception as e:
            logger.warning(f"Email send failed: {e}")


email_service = EmailService()
