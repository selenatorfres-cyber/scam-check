from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.monitor.client import Monitor

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, Message
from telethon.errors import SessionPasswordNeededError
from sqlalchemy import func, select

from app.bot.keyboards import (
    admin_keyboard,
    admin_management_keyboard,
    admin_remove_confirm_keyboard,
    published_keyboard,
    review_keyboard,
    source_keyboard,
    stats_keyboard,
    settings_keyboard,
    back_keyboard,
)
from app.config import settings
from app.database.db import get_admin, list_admins, log_event, session_scope, stats, get_setting, set_setting, get_target_chat_id
from app.database.models import Admin, Report, ReportStatus, Source

router = Router()


class AddSourceState(StatesGroup):
    waiting_for_source = State()


class LogoState(StatesGroup):
    waiting_for_logo = State()


class AddAdminState(StatesGroup):
    waiting_for_user_id = State()


class AccountBQrPasswordState(StatesGroup):
    waiting_for_password = State()


class TargetChatState(StatesGroup):
    waiting_for_chat = State()


async def get_role(user_id: int) -> str | None:
    async with session_scope() as session:
        admin = await get_admin(session, user_id)
        return admin.role if admin else None


async def is_admin(user_id: int) -> bool:
    return (await get_role(user_id)) in {"owner", "admin"}


async def is_owner(user_id: int) -> bool:
    return (await get_role(user_id)) == "owner"


async def deny(callback: CallbackQuery) -> None:
    """Reject unauthorized callbacks and remove stale inline buttons when possible.

    Telegram messages sent before a role/menu change can still display old buttons.
    Removing that keyboard prevents repeated attempts; every sensitive handler must
    still perform its own owner check.
    """
    try:
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        # The message may be inaccessible or already edited; authorization still fails.
        pass
    await callback.answer("⛔ Owner only. Access denied.", show_alert=True)


@router.message(Command("help"))
async def help_command(message: Message):
    """Give authorized admins a short guide to the Scam Monitor workflow."""
    if not message.from_user or not await is_admin(message.from_user.id):
        await message.answer("⛔ You are not authorized to use this bot.")
        return

    text = (
        "🛡️ <b>SCAM MONITOR — QUICK HELP</b>\n\n"
        "1. <b>Connect Account B:</b> the owner connects the Telegram account used to monitor sources.\n"
        "2. <b>Add source channels:</b> choose the channels Account B should monitor.\n"
        "3. <b>Review Group:</b> receives each report with complete report image, proof/profile links, and Approve/Reject buttons.\n"
        "4. <b>Main Channel:</b> receives only reports approved by an admin, with the configured logo and profile/proof buttons.\n"
        "5. <b>Monitor Channels:</b> add up to 3 source channels. Account B checks enabled sources and sends detected reports to the review group.\n"
        "6. <b>Approve / Reject:</b> approve publishes to the main channel; reject prevents publication.\n"
        "7. <b>Logo:</b> Change Logo updates the branding on newly generated report images before review.\n\n"
        "<b>Useful commands</b>\n"
        "/start — open the admin panel\n"
        "/help — show this guide\n"
        "/show_targets — view configured destinations\n"
        "/set_review_group @groupusername — set the review group\n"
        "/set_main_channel @channelusername — set the main channel\n\n"
        "Keep Account B connected and make sure the bot has permission to post in the review group and main channel."
    )
    await message.answer(text, parse_mode="HTML")


@router.message(CommandStart())
async def start(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("⛔ You are not authorized to use this bot.")
        return
    await message.answer(
        "⚙️ ADMIN PANEL\n\nUse Settings to connect the Telegram account "
        "that will monitor your source channels.",
        reply_markup=admin_keyboard(await is_owner(message.from_user.id)),
    )


def _normalize_chat_reference(raw: str) -> str:
    value = raw.strip()
    if value.startswith("https://t.me/") or value.startswith("http://t.me/"):
        value = value.split("t.me/", 1)[1].strip("/")
        value = value.split("?", 1)[0]
        if value.startswith("+") or value.startswith("joinchat/"):
            raise ValueError("Invite links cannot identify a chat reliably. Use the group's @username or numeric chat ID.")
    return value


async def _set_target_chat(message: Message, bot: Bot, key: str, label: str, raw: str) -> None:
    if not message.from_user or not await is_admin(message.from_user.id):
        await message.answer("⛔ Admin access required to change this setting.")
        return
    try:
        reference = _normalize_chat_reference(raw)
        chat = await bot.get_chat(reference if not reference.lstrip("-").isdigit() else int(reference))
        if key == "REVIEW_GROUP_ID" and chat.type not in {"group", "supergroup"}:
            await message.answer("Please provide a group/supergroup username, t.me link, or numeric chat ID.")
            return
        if key == "MAIN_CHANNEL_ID" and chat.type != "channel":
            await message.answer("Please provide a channel username, t.me link, or numeric channel ID.")
            return
        async with session_scope() as session:
            await set_setting(session, key, str(chat.id))
        await message.answer(f"✅ {label} saved: {chat.title or chat.full_name or chat.id} (`{chat.id}`).", parse_mode="Markdown")
    except Exception as exc:
        await message.answer(
            "❌ Couldn't resolve that chat. Add the bot to the group/channel first and grant the required permissions, "
            "then try its @username, public t.me link, or numeric chat ID.\n\n"
            f"Details: {type(exc).__name__}: {exc}"
        )


@router.message(Command("set_review_group"))
async def set_review_group_command(message: Message, bot: Bot):
    raw = (message.text or "").partition(" ")[2].strip()
    if not raw:
        await message.answer("Usage: /set_review_group @groupusername\nOr: /set_review_group -1001234567890\nThe bot must already be a member of the group.")
        return
    await _set_target_chat(message, bot, "REVIEW_GROUP_ID", "Review group", raw)


@router.message(Command("set_main_channel"))
async def set_main_channel_command(message: Message, bot: Bot):
    raw = (message.text or "").partition(" ")[2].strip()
    if not raw:
        await message.answer("Usage: /set_main_channel @channelusername\nOr: /set_main_channel -1001234567890\nThe bot must be an administrator of the channel to publish.")
        return
    await _set_target_chat(message, bot, "MAIN_CHANNEL_ID", "Main channel", raw)


@router.message(Command("show_targets"))
async def show_targets_command(message: Message):
    if not message.from_user or not await is_admin(message.from_user.id):
        await message.answer("⛔ Admin access required to view these settings.")
        return
    async with session_scope() as session:
        review_id = await get_target_chat_id(session, "REVIEW_GROUP_ID", settings.review_group_id)
        channel_id = await get_target_chat_id(session, "MAIN_CHANNEL_ID", settings.main_channel_id)
    await message.answer(f"Review group: {review_id or 'Not set'}\nMain channel: {channel_id or 'Not set'}")


@router.callback_query(F.data.startswith("target_set:"))
async def target_set_button(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await deny(callback)
        return
    key = callback.data.split(":", 1)[1]
    if key not in {"REVIEW_GROUP_ID", "MAIN_CHANNEL_ID"}:
        await callback.answer("Unknown setting", show_alert=True)
        return
    label = "review group" if key == "REVIEW_GROUP_ID" else "main channel"
    await state.set_state(TargetChatState.waiting_for_chat)
    await state.update_data(target_key=key)
    prompt = (
        f"Send the {label} username, public t.me link, or numeric chat ID.\n\n"
        "Examples: @mygroup or -1001234567890\n"
        "Make sure the bot is already in the group; for a channel, make it an administrator."
    )
    await callback.message.answer(prompt, reply_markup=back_keyboard("settings"))
    await callback.answer()


@router.message(TargetChatState.waiting_for_chat)
async def target_chat_received(message: Message, state: FSMContext, bot: Bot):
    if not message.from_user or not await is_admin(message.from_user.id):
        await state.clear()
        await message.answer("⛔ Admin access required to change destination settings.")
        return
    data = await state.get_data()
    key = data.get("target_key")
    if key not in {"REVIEW_GROUP_ID", "MAIN_CHANNEL_ID"}:
        await state.clear()
        await message.answer("Unknown destination setting. Open Owner Settings and try again.")
        return
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Send a username, public Telegram link, or numeric chat ID.")
        return
    await _set_target_chat(message, bot, key, "Review group" if key == "REVIEW_GROUP_ID" else "Main channel", raw)
    await state.clear()


async def _chat_display(bot: Bot, chat_id: int | None, label: str) -> str:
    """Show a configured chat's title and public username when Telegram exposes them."""
    if not chat_id:
        return f"{label}: ❌ Not set"
    try:
        chat = await bot.get_chat(chat_id)
        title = getattr(chat, "title", None) or getattr(chat, "full_name", None) or label
        username = getattr(chat, "username", None)
        username_text = f" (@{username})" if username else " (no public username)"
        return f"{label}: ✅ {title}{username_text}\nID: <code>{chat_id}</code>"
    except Exception:
        return f"{label}: ⚠️ Saved ID <code>{chat_id}</code> (name/username unavailable to bot)"


async def _workflow_preview_text(bot: Bot) -> str:
    async with session_scope() as session:
        review_id = await get_target_chat_id(session, "REVIEW_GROUP_ID", settings.review_group_id)
        main_id = await get_target_chat_id(session, "MAIN_CHANNEL_ID", settings.main_channel_id)
        sources = list(await session.scalars(select(Source).order_by(Source.id)))

    source_lines = []
    for index, source in enumerate(sources, start=1):
        display = source.channel_title or source.channel_username or str(source.channel_id)
        username = f" (@{source.channel_username.lstrip('@')})" if source.channel_username else " (no public username saved)"
        status = "🟢 ON" if source.enabled else "🔴 OFF"
        source_lines.append(f"{index}. {display}{username} — {status}\n   ID: <code>{source.channel_id}</code>")
    if not source_lines:
        source_text = "❌ No monitor channels configured yet."
    else:
        source_text = "\n".join(source_lines)

    review_text = await _chat_display(bot, review_id, "2️⃣ Review Group")
    main_text = await _chat_display(bot, main_id, "3️⃣ Main Channel (final destination)")
    return (
        "👁 <b>WORKFLOW PREVIEW</b>\n\n"
        "<b>1️⃣ Monitor Channels</b> — Account B checks these source channels:\n"
        f"{source_text}\n\n"
        f"<b>{review_text}</b>\n"
        "Detected reports are sent here with report details and Approve/Reject buttons.\n\n"
        f"<b>{main_text}</b>\n"
        "Only reports approved by an admin are published here with the configured logo.\n\n"
        "<b>Flow:</b> Monitor source → Review Group → Approve → Main Channel.\n"
        "Reject means the report is not published."
    )


@router.callback_query(F.data == "workflow_preview")
async def workflow_preview_button(callback: CallbackQuery, bot: Bot):
    if not await is_admin(callback.from_user.id):
        await deny(callback)
        return
    preview = await _workflow_preview_text(bot)
    await callback.message.answer(preview, parse_mode="HTML", reply_markup=back_keyboard("settings"))
    await callback.answer()


@router.callback_query(F.data == "target_show")
async def target_show_button(callback: CallbackQuery, bot: Bot):
    if not await is_admin(callback.from_user.id):
        await deny(callback)
        return
    preview = await _workflow_preview_text(bot)
    await callback.message.answer(preview, parse_mode="HTML", reply_markup=back_keyboard("settings"))
    await callback.answer()


@router.callback_query(F.data == "back_admin")
async def back_admin(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await deny(callback); return
    await callback.message.edit_text("⚙️ ADMIN PANEL", reply_markup=admin_keyboard(await is_owner(callback.from_user.id)))
    await callback.answer()


@router.callback_query(F.data == "back_settings")
async def back_settings(callback: CallbackQuery, state: FSMContext, monitor: Monitor):
    if not await is_admin(callback.from_user.id):
        await deny(callback); return
    await state.clear()
    await callback.message.edit_text(
        await account_b_text(monitor),
        reply_markup=settings_keyboard(await is_owner(callback.from_user.id)),
    )
    await callback.answer()


@router.callback_query(F.data == "back_sources")
async def back_sources(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await deny(callback); return
    await state.clear()
    async with session_scope() as s:
        rows = list(await s.scalars(select(Source).order_by(Source.id)))
    await callback.message.edit_text("📡 SOURCE CHANNELS", reply_markup=source_keyboard(rows))
    await callback.answer()


@router.callback_query(F.data == "cancel_setup")
async def cancel_setup(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await deny(callback); return
    await state.clear()
    await callback.answer("Cancelled")


@router.callback_query(F.data == "sources")
async def sources(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await deny(callback); return
    async with session_scope() as s:
        rows = list(await s.scalars(select(Source).order_by(Source.id)))
    await callback.message.edit_text("📡 SOURCE CHANNELS", reply_markup=source_keyboard(rows))
    await callback.answer()


@router.callback_query(F.data.startswith("toggle:"))
async def toggle_source(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await deny(callback); return
    source_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as s:
        source = await s.get(Source, source_id)
        if not source:
            await callback.answer("Source not found", show_alert=True); return
        source.enabled = not source.enabled
        await log_event(s, "SOURCE_ENABLED" if source.enabled else "SOURCE_DISABLED", admin_id=callback.from_user.id, metadata={"source_id": source.id})
        rows = list(await s.scalars(select(Source).order_by(Source.id)))
    await callback.message.edit_text("📡 SOURCE CHANNELS", reply_markup=source_keyboard(rows))
    await callback.answer("Updated")


@router.callback_query(F.data == "add_source")
async def add_source(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await deny(callback); return
    await state.set_state(AddSourceState.waiting_for_source)
    await callback.message.answer("Send the source channel username, public URL, or numeric channel ID.", reply_markup=back_keyboard("back_sources"))
    await callback.answer()


@router.message(AddSourceState.waiting_for_source)
async def add_source_message(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await message.answer("⛔ You are not authorized to use this bot."); return
    value = (message.text or "").strip()
    channel_id = None
    username = None
    if value.lstrip("-").isdigit():
        channel_id = int(value)
    else:
        username = value.rstrip("/").rsplit("/", 1)[-1].lstrip("@").strip()
    if not channel_id and not username:
        await message.answer("Invalid source. Try @channelname or a numeric channel ID."); return
    async with session_scope() as s:
        total = await s.scalar(select(func.count(Source.id))) or 0
        existing = None
        if username:
            existing = await s.scalar(select(Source).where(Source.channel_username == username))
        if channel_id:
            existing = await s.scalar(select(Source).where(Source.channel_id == channel_id))
        if total >= 3 and existing is None:
            await message.answer("Maximum of 3 source channels reached."); await state.clear(); return
        if existing:
            await message.answer("That source already exists."); await state.clear(); return
        placeholder_id = channel_id
        if placeholder_id is None:
            # Telethon channel IDs are positive in this project; use a unique negative
            # placeholder until Account B resolves the username to the real channel ID.
            min_id = await s.scalar(select(func.min(Source.channel_id)))
            placeholder_id = (int(min_id) - 1) if min_id is not None and int(min_id) < 0 else -1
            while await s.scalar(select(Source.id).where(Source.channel_id == placeholder_id)):
                placeholder_id -= 1
        s.add(Source(channel_id=placeholder_id, channel_username=username, enabled=True))
        await log_event(s, "SOURCE_ADDED", admin_id=message.from_user.id, metadata={"value": value})
    await state.clear()
    await message.answer("✅ Source saved. The monitor will resolve a username-based source through Account B.")


@router.callback_query(F.data == "pending")
async def pending(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await deny(callback); return
    async with session_scope() as s:
        rows = list(await s.scalars(select(Report).where(Report.status.in_([ReportStatus.PENDING.value, ReportStatus.NEEDS_REVIEW.value])).order_by(Report.created_at.desc()).limit(20)))
    if not rows:
        text = "⏳ Pending Reports: 0"
    else:
        text = "\n".join([f"⏳ Pending Reports: {len(rows)}"] + [f"#{r.id} @{r.username or 'unknown'} ({r.status})" for r in rows])
    await callback.message.edit_text(text, reply_markup=admin_keyboard(await is_owner(callback.from_user.id)))
    await callback.answer()


@router.callback_query(F.data == "stats")
async def stats_menu(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await deny(callback); return
    await callback.message.edit_text("📊 STATISTICS", reply_markup=stats_keyboard())
    await callback.answer()


def format_stats(title: str, data: dict) -> str:
    lines = [title, "", f"📥 Total Notifications: {data['total']}", "", "📡 SOURCE"]
    lines += [f"{name}: {count}" for name, count in data["sources"]] or ["No data"]
    lines += ["", "📋 DECISIONS", f"✅ Approved: {data['approved']}", f"❌ Rejected: {data['rejected']}", f"⏳ Pending: {data['pending']}", f"📤 Published: {data['published']}", "", f"Approval Rate: {data['approval_rate']:.1f}%", f"Rejection Rate: {data['rejection_rate']:.1f}%"]
    return "\n".join(lines)


@router.callback_query(F.data == "stats_week")
async def stats_week(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await deny(callback); return
    now = datetime.now(timezone.utc); start = now - timedelta(days=7)
    async with session_scope() as s: data = await stats(s, start, now)
    await callback.message.edit_text(format_stats("📊 WEEKLY STATISTICS", data), reply_markup=admin_keyboard(await is_owner(callback.from_user.id)))
    await callback.answer()


@router.callback_query(F.data == "stats_month")
async def stats_month(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await deny(callback); return
    now = datetime.now(timezone.utc); start = now - timedelta(days=30)
    async with session_scope() as s: data = await stats(s, start, now)
    await callback.message.edit_text(format_stats("📊 MONTHLY STATISTICS", data), reply_markup=admin_keyboard(await is_owner(callback.from_user.id)))
    await callback.answer()


@router.callback_query(F.data == "change_logo")
async def change_logo(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await deny(callback); return
    await state.set_state(LogoState.waiting_for_logo)
    await callback.message.answer("Send the new logo as an image.", reply_markup=back_keyboard("back_admin"))
    await callback.answer()


@router.message(LogoState.waiting_for_logo, F.photo)
async def receive_logo_photo(message: Message, state: FSMContext, bot: Bot):
    if not await is_admin(message.from_user.id):
        return
    target = Path(settings.default_logo); target.parent.mkdir(parents=True, exist_ok=True)
    await bot.download(message.photo[-1], destination=target)
    await state.clear()
    await message.answer("✅ Logo updated.")



# ---------------- Account B setup ----------------

async def account_b_text(monitor: Monitor) -> str:
    try:
        me = await monitor.account_info()
    except Exception:
        me = None
    if me:
        username = f"@{me.username}" if getattr(me, "username", None) else "(no username)"
        return (
            "📡 ACCOUNT B\n\n"
            "Status: 🟢 CONNECTED\n"
            f"Account: {username}\n"
            f"Telegram ID: {me.id}\n\n"
            "This account is used to monitor your selected source channels."
        )
    return (
        "📡 ACCOUNT B\n\n"
        "Status: 🔴 NOT CONNECTED\n\n"
        "Connect your normal Telegram account using Telegram QR login.\n\n"
        "You will scan the QR code with the Telegram app where that account "
        "is already logged in."
    )


@router.callback_query(F.data == "settings")
async def settings_menu(callback: CallbackQuery, monitor: Monitor):
    if not await is_admin(callback.from_user.id):
        await deny(callback)
        return
    # The three workflow destinations/options are available to every admin;
    # only Account B connection and admin management remain owner-only.
    header = "🎯 DESTINATIONS & MONITORING\n\n1. Review Group — receives reports for approval/rejection.\n2. Main Channel — receives approved reports.\n3. Monitor Channels — add up to 3 source channels."
    if await is_owner(callback.from_user.id):
        header = await account_b_text(monitor) + "\n\n" + header
    await callback.message.edit_text(
        header,
        reply_markup=settings_keyboard(await is_owner(callback.from_user.id)),
    )
    await callback.answer()


@router.callback_query(F.data == "monitor_channels")
async def monitor_channels_menu(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await deny(callback)
        return
    async with session_scope() as session:
        rows = list(await session.scalars(select(Source).order_by(Source.id)))
    await callback.message.edit_text(
        f"3️⃣ MONITOR CHANNELS\n\nConfigured: {len(rows)}/3\nAdd up to three channels. Enabled channels are monitored by Account B; detected reports go to the Review Group for approval/rejection.",
        reply_markup=source_keyboard(rows),
    )
    await callback.answer()


async def watch_qr_login(chat_id: int, user_id: int, bot: Bot, state: FSMContext, monitor: Monitor):
    result = await monitor.wait_qr_result()
    if not result:
        return
    if not await is_admin(user_id):
        return

    if result.get("status") == "authorized":
        await state.clear()
        me = result["me"]
        username = f"@{me.username}" if getattr(me, "username", None) else "(no username)"
        await bot.send_message(
            chat_id,
            "✅ Account B connected successfully!\n\n"
            f"Account: {username}\n"
            f"Telegram ID: {me.id}\n\n"
            "You can now add source channels.",
            reply_markup=back_keyboard("back_settings"),
        )
        return

    if result.get("status") == "password":
        await state.set_state(AccountBQrPasswordState.waiting_for_password)
        await bot.send_message(
            chat_id,
            "🔐 Account B has Telegram 2-Step Verification enabled.\n\n"
            "Send the 2-Step Verification password to finish the QR login.\n\n"
            "⚠️ This is sensitive. Do not forward or share it.",
            reply_markup=back_keyboard("back_settings"),
        )
        return

    await state.clear()
    await bot.send_message(
        chat_id,
        f"❌ Account B QR login failed.\n\n{result.get('error', 'Unknown error')}",
        reply_markup=back_keyboard("back_settings"),
    )


@router.callback_query(F.data == "connect_account")
async def connect_account(callback: CallbackQuery, state: FSMContext, monitor: Monitor):
    if not await is_owner(callback.from_user.id):
        await deny(callback)
        return

    try:
        result = await monitor.begin_qr_login()
    except Exception as exc:
        await callback.answer("Could not start QR login", show_alert=True)
        await callback.message.answer(f"❌ Could not start Account B QR login.\n\n{exc}")
        return

    if result.get("authorized"):
        me = result["me"]
        username = f"@{me.username}" if getattr(me, "username", None) else "(no username)"
        await state.clear()
        await callback.message.answer(
            "✅ Account B is already connected.\n\n"
            f"Account: {username}\n"
            f"Telegram ID: {me.id}",
            reply_markup=back_keyboard("back_settings"),
        )
        await callback.answer()
        return

    qr_image = BufferedInputFile(result["image"], filename="account_b_qr.png")
    expires = result.get("expires")
    remaining = "about 2 minutes"
    if expires is not None:
        try:
            seconds = max(1, int((expires - datetime.now(expires.tzinfo)).total_seconds()))
            remaining = f"about {max(1, seconds // 60)} minute(s)"
        except Exception:
            pass

    await state.clear()
    asyncio.create_task(watch_qr_login(callback.message.chat.id, callback.from_user.id, callback.bot, state, monitor))

    await callback.message.answer_photo(
        qr_image,
        caption=(
            "🔗 CONNECT ACCOUNT B\n\n"
            "1. Open Telegram on your phone or another device where the Account B "
            "account is already logged in.\n"
            "2. Go to Settings → Devices → Link Desktop Device.\n"
            "3. Scan the QR code below.\n\n"
            f"⏱️ This QR code is valid for {remaining}.\n\n"
            "⚠️ Never forward this QR image or login link to anyone else."
        ),
        reply_markup=back_keyboard("back_settings"),
    )
    await callback.answer("QR code generated")


@router.message(AccountBQrPasswordState.waiting_for_password)
async def receive_account_qr_password(message: Message, state: FSMContext, monitor: Monitor):
    if not await is_admin(message.from_user.id):
        await state.clear()
        await message.answer("⛔ You are not authorized to use this bot.")
        return

    password = message.text or ""
    if not password:
        await message.answer("Please send the Telegram 2-Step Verification password.", reply_markup=back_keyboard("back_settings"))
        return

    try:
        result = await monitor.complete_qr_login_password(password)
    except Exception as exc:
        await state.clear()
        await message.answer(f"❌ Telegram 2-Step Verification failed.\n\n{exc}", reply_markup=back_keyboard("back_settings"))
        return
    finally:
        try:
            await message.delete()
        except Exception:
            pass

    await state.clear()
    me = result["me"]
    username = f"@{me.username}" if getattr(me, "username", None) else "(no username)"
    await message.answer(
        "✅ Account B connected successfully!\n\n"
        f"Account: {username}\n"
        f"Telegram ID: {me.id}\n\n"
        "You can now add source channels.",
        reply_markup=back_keyboard("back_settings"),
    )


@router.callback_query(F.data == "disconnect_account")
async def disconnect_account(callback: CallbackQuery, monitor: Monitor):
    if not await is_owner(callback.from_user.id):
        await deny(callback)
        return
    try:
        await monitor.disconnect_account()
        await callback.message.edit_text(
            "📡 ACCOUNT B\n\nStatus: 🔴 DISCONNECTED",
            reply_markup=settings_keyboard(True),
        )
        await callback.answer("Account B disconnected.")
    except Exception as exc:
        await callback.answer(f"Disconnect failed: {exc}", show_alert=True)


# ---------------- Owner/Admin management ----------------

@router.callback_query(F.data == "admins")
async def admins(callback: CallbackQuery):
    if not await is_owner(callback.from_user.id):
        await deny(callback); return
    async with session_scope() as s:
        rows = await list_admins(s)
    lines = ["👥 ADMIN MANAGEMENT", "", "👑 Owner", str(settings.owner_id), "", "👤 Admins"]
    regular = [a for a in rows if a.role == "admin"]
    lines.extend(str(a.telegram_user_id) for a in regular) if regular else lines.append("None")
    await callback.message.edit_text("\n".join(lines), reply_markup=admin_management_keyboard(rows))
    await callback.answer()


@router.callback_query(F.data == "admin_add")
async def admin_add(callback: CallbackQuery, state: FSMContext):
    if not await is_owner(callback.from_user.id):
        await deny(callback); return
    await state.set_state(AddAdminState.waiting_for_user_id)
    await callback.message.answer("Send the Telegram numeric User ID of the new admin.", reply_markup=back_keyboard("admins"))
    await callback.answer()


@router.message(AddAdminState.waiting_for_user_id)
async def receive_admin_id(message: Message, state: FSMContext):
    if not await is_owner(message.from_user.id):
        await message.answer("⛔ Only the owner can manage administrators."); return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Invalid ID. Send a numeric Telegram User ID.")
        return
    user_id = int(raw)
    if user_id == settings.owner_id:
        await message.answer("That user is already the owner.")
        await state.clear(); return
    async with session_scope() as s:
        existing = await s.scalar(select(Admin).where(Admin.telegram_user_id == user_id))
        if existing:
            existing.enabled = True
            existing.role = "admin"
        else:
            s.add(Admin(telegram_user_id=user_id, role="admin", enabled=True, created_by=message.from_user.id))
        await log_event(s, "ADMIN_ADDED", admin_id=message.from_user.id, metadata={"target_admin_id": user_id})
    await state.clear()
    await message.answer(f"✅ Admin added\n\nUser ID: {user_id}")


@router.callback_query(F.data.startswith("admin_remove:"))
async def admin_remove(callback: CallbackQuery):
    if not await is_owner(callback.from_user.id):
        await deny(callback); return
    user_id = int(callback.data.split(":", 1)[1])
    if user_id == settings.owner_id:
        await callback.answer("The owner cannot be removed.", show_alert=True); return
    await callback.message.edit_text(f"⚠️ Remove this administrator?\n\nUser ID: {user_id}", reply_markup=admin_remove_confirm_keyboard(user_id))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_confirm_remove:"))
async def admin_confirm_remove(callback: CallbackQuery):
    if not await is_owner(callback.from_user.id):
        await deny(callback); return
    user_id = int(callback.data.split(":", 1)[1])
    if user_id == settings.owner_id:
        await callback.answer("The owner cannot be removed.", show_alert=True); return
    async with session_scope() as s:
        admin = await s.scalar(select(Admin).where(Admin.telegram_user_id == user_id))
        if not admin:
            await callback.answer("Admin not found.", show_alert=True); return
        admin.enabled = False
        await log_event(s, "ADMIN_REMOVED", admin_id=callback.from_user.id, metadata={"target_admin_id": user_id})
        rows = await list_admins(s)
    await callback.message.edit_text("👥 ADMIN MANAGEMENT", reply_markup=admin_management_keyboard(rows))
    await callback.answer("Admin removed")


@router.callback_query(F.data.startswith("noop:"))
async def noop(callback: CallbackQuery):
    await callback.answer("Proof unavailable for this report.", show_alert=True)


async def handle_decision(callback: CallbackQuery, bot: Bot, approve: bool):
    if not await is_admin(callback.from_user.id):
        await deny(callback); return
    report_id = int(callback.data.split(":", 1)[1])
    async with session_scope() as s:
        report = await s.get(Report, report_id)
        if not report:
            await callback.answer("Report not found", show_alert=True); return
        if report.status not in {ReportStatus.PENDING.value, ReportStatus.NEEDS_REVIEW.value}:
            await callback.answer(f"Already {report.status.lower()}", show_alert=True); return
        if approve:
            image_path = report.generated_image_path
            try:
                target_id = await get_target_chat_id(s, "MAIN_CHANNEL_ID", settings.main_channel_id)
                if not target_id:
                    raise RuntimeError("Main channel is not configured. Use /set_main_channel @channelusername")
                sent = await bot.send_photo(target_id, FSInputFile(image_path), reply_markup=published_keyboard(report.profile_url, report.proof_url))
                report.status = ReportStatus.APPROVED.value
                report.approved_by = callback.from_user.id
                report.approved_at = datetime.now(timezone.utc)
                report.published_message_id = sent.message_id
                report.published_message_url = f"https://t.me/c/{str(target_id)[4:]}/{sent.message_id}" if str(target_id).startswith("-100") else None
                await log_event(s, "APPROVED", report.id, callback.from_user.id)
                await log_event(s, "PUBLISHED", report.id, callback.from_user.id)
                await callback.message.edit_caption(caption="✅ APPROVED\n\nPublished successfully.", reply_markup=None)
            except Exception as exc:
                report.status = ReportStatus.PUBLISH_FAILED.value
                await log_event(s, "PUBLISH_FAILED", report.id, callback.from_user.id, {"error": str(exc)})
                await callback.message.edit_caption(caption="⚠️ PUBLISH FAILED\n\nThe report was not confirmed as published.", reply_markup=None)
                await callback.answer("Publication failed", show_alert=True); return
        else:
            report.status = ReportStatus.REJECTED.value
            report.rejected_by = callback.from_user.id
            report.rejected_at = datetime.now(timezone.utc)
            await log_event(s, "REJECTED", report.id, callback.from_user.id)
            await callback.message.edit_caption(caption="❌ REJECTED", reply_markup=None)
    await callback.answer("Approved" if approve else "Rejected")


@router.callback_query(F.data.startswith("approve:"))
async def approve(callback: CallbackQuery, bot: Bot):
    await handle_decision(callback, bot, True)


@router.callback_query(F.data.startswith("reject:"))
async def reject(callback: CallbackQuery, bot: Bot):
    await handle_decision(callback, bot, False)


async def publish_review(bot: Bot, report: Report) -> Message:
    caption = f"❌ @{report.username} ({report.user_id}) has been marked as a scammer." if report.username and report.user_id else f"❌ {('@' + report.username) if report.username else 'Unknown user'} has been marked as a scammer."
    async with session_scope() as session:
        target_id = await get_target_chat_id(session, "REVIEW_GROUP_ID", settings.review_group_id)
    if not target_id:
        raise RuntimeError("Review group is not configured. Use /set_review_group @groupusername")
    return await bot.send_photo(target_id, FSInputFile(report.generated_image_path), caption=caption, reply_markup=review_keyboard(report.id, report.profile_url, report.proof_url))


async def run_bot(bot: Bot, monitor: Monitor) -> None:
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await dp.start_polling(bot, monitor=monitor)
    finally:
        await bot.session.close()
