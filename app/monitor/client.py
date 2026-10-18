from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from sqlalchemy import select

from app.bot.app import publish_review
from app.config import settings
from app.database.db import get_target_chat_id, log_event, session_scope
from app.database.models import Report, ReportStatus, Source
from app.monitor.parser import parse_message
from app.reports.generator import generate_report

log = logging.getLogger(__name__)


class Monitor:
    """Owns the single Telethon client used for Account B login and monitoring."""

    def __init__(self, bot):
        self.bot = bot
        session_path = settings.mtproto_session
        if not session_path.endswith(".session"):
            session_path = str(Path(session_path))
        Path(session_path).parent.mkdir(parents=True, exist_ok=True)

        self.client = TelegramClient(
            session_path,
            settings.api_id,
            settings.api_hash,
        )
        self.authorized_event = asyncio.Event()
        self.qr_login = None
        self.qr_wait_task: asyncio.Task | None = None
        self.qr_result: asyncio.Future | None = None

    async def begin_qr_login(self):
        """Start Telethon QR authentication without collecting an OTP in the bot chat."""
        if not self.client.is_connected():
            await self.client.connect()

        if await self.client.is_user_authorized():
            me = await self.client.get_me()
            self.authorized_event.set()
            return {"authorized": True, "me": me}

        if self.qr_wait_task and not self.qr_wait_task.done():
            self.qr_wait_task.cancel()
            try:
                await self.qr_wait_task
            except asyncio.CancelledError:
                pass

        self.qr_login = await self.client.qr_login()
        self.qr_result = asyncio.get_running_loop().create_future()
        self.qr_wait_task = asyncio.create_task(self._wait_for_qr_login(self.qr_login, self.qr_result))

        import qrcode
        from io import BytesIO

        qr = qrcode.QRCode(version=None, box_size=10, border=4)
        qr.add_data(self.qr_login.url)
        qr.make(fit=True)
        image = qr.make_image()
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)

        return {
            "authorized": False,
            "image": buffer.getvalue(),
            "expires": self.qr_login.expires,
        }

    async def _wait_for_qr_login(self, qr_login, result_future):
        try:
            me = await qr_login.wait()
            self.authorized_event.set()
            if not result_future.done():
                result_future.set_result({"status": "authorized", "me": me})
        except SessionPasswordNeededError:
            if not result_future.done():
                result_future.set_result({"status": "password"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Account B QR login failed")
            if not result_future.done():
                result_future.set_result({"status": "error", "error": str(exc)})

    async def complete_qr_login_password(self, password: str):
        if not self.client.is_connected():
            await self.client.connect()
        await self.client.sign_in(password=password)
        me = await self.client.get_me()
        self.authorized_event.set()
        if self.qr_result and not self.qr_result.done():
            self.qr_result.set_result({"status": "authorized", "me": me})
        return {"me": me}

    async def wait_qr_result(self):
        if not self.qr_result:
            return None
        return await self.qr_result

    async def qr_status(self):
        if not self.qr_result:
            return None
        if not self.qr_result.done():
            return {"status": "waiting"}
        return self.qr_result.result()

    async def account_info(self):
        if not self.client.is_connected():
            await self.client.connect()
        if not await self.client.is_user_authorized():
            return None
        return await self.client.get_me()

    async def disconnect_account(self):
        self.authorized_event.clear()
        if self.qr_wait_task and not self.qr_wait_task.done():
            self.qr_wait_task.cancel()
            try:
                await self.qr_wait_task
            except asyncio.CancelledError:
                pass
        self.qr_wait_task = None
        self.qr_login = None
        self.qr_result = None
        if self.client.is_connected() and await self.client.is_user_authorized():
            await self.client.log_out()
        elif self.client.is_connected():
            await self.client.disconnect()

    async def _enabled_source_ids(self) -> set[int]:
        async with session_scope() as s:
            rows = await s.scalars(select(Source).where(Source.enabled.is_(True)))
            return {x.channel_id for x in rows if x.channel_id}

    async def _resolve_sources(self):
        async with session_scope() as s:
            rows = list(await s.scalars(select(Source).where(Source.enabled.is_(True))))

        for source in rows:
            if source.channel_id and source.channel_id > 0:
                continue
            if not source.channel_username:
                continue
            try:
                entity = await self.client.get_entity(source.channel_username)
                channel_id = int(getattr(entity, "id"))
                async with session_scope() as s:
                    db_source = await s.scalar(select(Source).where(Source.id == source.id))
                    if db_source:
                        db_source.channel_id = channel_id
                        db_source.channel_title = getattr(entity, "title", None)
            except Exception:
                log.exception("Unable to resolve source %s", source.channel_username)

    async def start(self):
        """Wait for Account B authentication, then begin monitoring."""
        if not self.client.is_connected():
            await self.client.connect()

        if await self.client.is_user_authorized():
            self.authorized_event.set()

        log.info("Account B monitor waiting for authentication.")

        await self.authorized_event.wait()

        await self._resolve_sources()

        @self.client.on(events.NewMessage)
        async def on_message(event):
            await self.process_event(event)

        me = await self.client.get_me()
        log.info(
            "Account B monitor started as @%s (id=%s)",
            getattr(me, "username", None),
            getattr(me, "id", None),
        )

        await self.client.run_until_disconnected()

    async def process_event(self, event):
        chat = await event.get_chat()
        channel_id = getattr(chat, "id", None)
        if channel_id is None:
            return

        async with session_scope() as s:
            source = await s.scalar(
                select(Source).where(
                    Source.channel_id == channel_id,
                    Source.enabled.is_(True),
                )
            )
            if not source:
                return
            if not getattr(chat, "broadcast", False):
                return
            source_username = getattr(chat, "username", None) or source.channel_username

        parsed = parse_message(event.message, source_username)
        if not parsed.is_report:
            return

        async with session_scope() as s:
            duplicate = await s.scalar(
                select(Report).where(
                    Report.source_channel_id == channel_id,
                    Report.source_message_id == event.message.id,
                )
            )
            if duplicate:
                await log_event(s, "DUPLICATE_DETECTED", duplicate.id)
                return

            status = ReportStatus.PENDING.value
            if not parsed.username and not parsed.user_id:
                status = ReportStatus.NEEDS_REVIEW.value

            report = Report(
                username=parsed.username,
                user_id=parsed.user_id,
                profile_url=parsed.profile_url,
                proof_url=parsed.proof_url,
                source_channel_id=channel_id,
                source_channel_username=source_username,
                source_message_id=event.message.id,
                source_message_url=parsed.source_message_url,
                status=status,
            )
            s.add(report)
            await s.flush()
            report.generated_image_path = generate_report(
                report.id,
                report.username,
                report.user_id,
                settings.default_logo,
            )
            await log_event(
                s,
                "REPORT_RECEIVED",
                report.id,
                metadata={"confidence": parsed.confidence},
            )
            await s.flush()

            try:
                review = await publish_review(self.bot, report)
                async with session_scope() as target_session:
                    report.review_group_id = await get_target_chat_id(target_session, "REVIEW_GROUP_ID", settings.review_group_id)
                report.review_message_id = review.message_id
                await log_event(s, "REVIEW_SENT", report.id)
            except Exception as exc:
                await log_event(
                    s,
                    "REVIEW_SEND_FAILED",
                    report.id,
                    metadata={"error": str(exc)},
                )
                log.exception("Failed to send review for report %s", report.id)


async def run_monitor(bot, monitor: Monitor):
    await monitor.start()
