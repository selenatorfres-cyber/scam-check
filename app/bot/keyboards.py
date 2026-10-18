from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def review_keyboard(report_id: int, profile_url: str | None, proof_url: str | None):
    b = InlineKeyboardBuilder()
    if profile_url:
        b.row(InlineKeyboardButton(text="👤 View Profile", url=profile_url))
    if proof_url:
        b.row(InlineKeyboardButton(text="📄 View Proofs", url=proof_url))
    else:
        b.row(InlineKeyboardButton(text="⚠️ Proof Unavailable", callback_data=f"noop:{report_id}"))
    b.row(
        InlineKeyboardButton(text="✅ APPROVE", callback_data=f"approve:{report_id}"),
        InlineKeyboardButton(text="❌ REJECT", callback_data=f"reject:{report_id}"),
    )
    return b.as_markup()


def published_keyboard(profile_url: str | None, proof_url: str | None):
    b = InlineKeyboardBuilder()
    row = []
    if profile_url:
        row.append(InlineKeyboardButton(text="👤 View Profile", url=profile_url))
    if proof_url:
        row.append(InlineKeyboardButton(text="📄 View Proofs", url=proof_url))
    if row:
        b.row(*row)
    return b.as_markup()


def settings_keyboard(is_owner: bool = False):
    b = InlineKeyboardBuilder()
    # Shared settings are available to all authorized admins.
    # Account B authentication and admin management remain owner-only.
    if is_owner:
        b.row(InlineKeyboardButton(text="🔗 Connect Account B", callback_data="connect_account"))
        b.row(InlineKeyboardButton(text="🗑 Disconnect Account B", callback_data="disconnect_account"))
        b.row(InlineKeyboardButton(text="👥 Manage Admins", callback_data="admins"))
    b.row(InlineKeyboardButton(text="1️⃣ Review Group — approvals", callback_data="target_set:REVIEW_GROUP_ID"))
    b.row(InlineKeyboardButton(text="2️⃣ Main Channel — approved posts", callback_data="target_set:MAIN_CHANNEL_ID"))
    b.row(InlineKeyboardButton(text="3️⃣ Monitor Channels — max 3", callback_data="monitor_channels"))
    b.row(InlineKeyboardButton(text="🎯 View Destinations", callback_data="target_show"))
    b.row(InlineKeyboardButton(text="👁 Preview Workflow", callback_data="workflow_preview"))
    b.row(InlineKeyboardButton(text="◀️ Back", callback_data="back_admin"))
    return b.as_markup()


def admin_keyboard(is_owner: bool = False):
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📡 Source Channels", callback_data="sources"))
    b.row(InlineKeyboardButton(text="📊 Statistics", callback_data="stats"))
    b.row(InlineKeyboardButton(text="⏳ Pending Reports", callback_data="pending"))
    b.row(InlineKeyboardButton(text="🖼 Change Logo", callback_data="change_logo"))
    b.row(InlineKeyboardButton(text="⚙️ Destinations & Monitoring", callback_data="settings"))
    if is_owner:
        b.row(InlineKeyboardButton(text="👥 Manage Admins", callback_data="admins"))
    return b.as_markup()


def source_keyboard(sources):
    b = InlineKeyboardBuilder()
    for s in sources:
        state = "🟢 ON" if s.enabled else "🔴 OFF"
        b.row(InlineKeyboardButton(text=f"{s.channel_username or s.channel_id} {state}", callback_data=f"toggle:{s.id}"))
    b.row(InlineKeyboardButton(text="➕ Add Source (maximum 3)", callback_data="add_source"))
    b.row(InlineKeyboardButton(text="◀️ Back", callback_data="back_admin"))
    return b.as_markup()


def stats_keyboard():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📊 This Week", callback_data="stats_week"), InlineKeyboardButton(text="📊 This Month", callback_data="stats_month"))
    b.row(InlineKeyboardButton(text="◀️ Back", callback_data="back_admin"))
    return b.as_markup()


def back_keyboard(callback_data: str = "back_admin"):
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="◀️ Back", callback_data=callback_data))
    return b.as_markup()


def admin_management_keyboard(admins):
    b = InlineKeyboardBuilder()
    for admin in admins:
        if admin.role == "owner":
            continue
        b.row(InlineKeyboardButton(text=f"👤 {admin.telegram_user_id}", callback_data=f"admin_remove:{admin.telegram_user_id}"))
    b.row(InlineKeyboardButton(text="➕ Add Admin", callback_data="admin_add"))
    b.row(InlineKeyboardButton(text="◀️ Back", callback_data="back_admin"))
    return b.as_markup()


def admin_remove_confirm_keyboard(user_id: int):
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="✅ Confirm", callback_data=f"admin_confirm_remove:{user_id}"), InlineKeyboardButton(text="❌ Cancel", callback_data="admins"))
    return b.as_markup()
