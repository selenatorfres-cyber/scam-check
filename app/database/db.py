from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database.models import Admin, Base, Event, Report, ReportStatus, Source, Setting

engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope():
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def log_event(session: AsyncSession, event_type: str, report_id: int | None = None, admin_id: int | None = None, metadata: dict | None = None):
    session.add(Event(report_id=report_id, event_type=event_type, admin_id=admin_id,
                      metadata_json=json.dumps(metadata or {}, default=str)))


async def get_source(session: AsyncSession, channel_id: int) -> Source | None:
    return await session.scalar(select(Source).where(Source.channel_id == channel_id))


async def enabled_sources(session: AsyncSession) -> list[Source]:
    result = await session.scalars(select(Source).where(Source.enabled.is_(True)).order_by(Source.id))
    return list(result)


async def get_setting(session: AsyncSession, key: str, default: str | None = None) -> str | None:
    row = await session.get(Setting, key)
    return row.value if row else default


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(Setting, key)
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))


async def stats(session: AsyncSession, start: datetime, end: datetime, source_id: int | None = None) -> dict:
    filters = [Report.created_at >= start, Report.created_at < end]
    if source_id is not None:
        filters.append(Report.source_channel_id == source_id)

    total = await session.scalar(select(func.count(Report.id)).where(*filters)) or 0
    approved = await session.scalar(select(func.count(Report.id)).where(*filters, Report.status == ReportStatus.APPROVED.value)) or 0
    rejected = await session.scalar(select(func.count(Report.id)).where(*filters, Report.status == ReportStatus.REJECTED.value)) or 0
    pending = await session.scalar(select(func.count(Report.id)).where(*filters, Report.status.in_([ReportStatus.PENDING.value, ReportStatus.NEEDS_REVIEW.value]))) or 0
    published = await session.scalar(select(func.count(Report.id)).where(*filters, Report.published_message_id.is_not(None))) or 0

    source_rows = await session.execute(
        select(Report.source_channel_username, func.count(Report.id))
        .where(*filters).group_by(Report.source_channel_username).order_by(func.count(Report.id).desc())
    )
    sources = [(name or str(source_id or "Unknown"), count) for name, count in source_rows.all()]
    decisions = approved + rejected
    return {
        "total": total, "approved": approved, "rejected": rejected, "pending": pending,
        "published": published, "approval_rate": (approved / decisions * 100 if decisions else 0),
        "rejection_rate": (rejected / decisions * 100 if decisions else 0), "sources": sources,
    }


async def seed_admins() -> None:
    async with session_scope() as session:
        owner = await session.scalar(select(Admin).where(Admin.telegram_user_id == settings.owner_id))
        if owner is None:
            session.add(Admin(telegram_user_id=settings.owner_id, role="owner", enabled=True, created_by=settings.owner_id))
        else:
            owner.role = "owner"
            owner.enabled = True
        for admin_id in settings.admins:
            if admin_id == settings.owner_id:
                continue
            row = await session.scalar(select(Admin).where(Admin.telegram_user_id == admin_id))
            if row is None:
                session.add(Admin(telegram_user_id=admin_id, role="admin", enabled=True, created_by=settings.owner_id))


async def get_admin(session: AsyncSession, telegram_user_id: int) -> Admin | None:
    return await session.scalar(select(Admin).where(Admin.telegram_user_id == telegram_user_id, Admin.enabled.is_(True)))


async def list_admins(session: AsyncSession) -> list[Admin]:
    rows = await session.scalars(select(Admin).where(Admin.enabled.is_(True)).order_by(Admin.role.desc(), Admin.telegram_user_id))
    return list(rows)


async def get_target_chat_id(session: AsyncSession, key: str, fallback: int = 0) -> int:
    """Read a chat ID configured in the bot, falling back to its environment value."""
    value = await get_setting(session, key)
    if value:
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    return int(fallback or 0)
