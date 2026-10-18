# Telegram Scam Report Monitor

Production-oriented starter implementation for a two-account Telegram workflow:

- Account B: Telethon/MTProto user client monitors only configured source channels.
- Bot: aiogram Bot API handles review, approval/rejection, publishing and analytics.
- SQLite by default, PostgreSQL-compatible SQLAlchemy architecture.
- Pillow generates a clean custom report image.

## Setup

1. Install Python 3.11+.
2. `python -m venv .venv && source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and fill in credentials.
5. Put your default logo at the path in `DEFAULT_LOGO` or use the admin logo upload.
6. Run `python -m app.main`.

For Account B, the first Telethon login may require an interactive phone/code/2FA login if `MTPROTO_SESSION` is not already supplied. The account must legitimately have access to monitored channels.

## Important

- Never commit `.env` or a Telethon session.
- The bot does not use the BotFather owner's user session for monitoring.
- Add source channels through the bot after startup.
- Add the bot to the private review group and main channel with the required permissions.


## Owner and Admin system

The bot supports two roles:

- `owner`: the Telegram ID in `OWNER_ID`. This account cannot be removed and is the only account allowed to add/remove admins.
- `admin`: administrators stored in the database. They can review reports, approve/reject, manage sources, change the logo, and view statistics.

Set the owner and optional initial admins in `.env`:

```env
OWNER_ID=123456789
ADMIN_IDS=987654321,555555555
```

On startup, these IDs are seeded into the database. After startup, use `👥 Manage Admins` from the owner account to add/remove admins. Removing an admin disables the account in the database instead of deleting its audit history.

The owner account must not be removed through the bot. Every admin-management callback and message handler checks the database-backed role.


## Configure review group and main channel from Telegram

`REVIEW_GROUP_ID` and `MAIN_CHANNEL_ID` are now optional environment variables. The owner can configure them after deployment by messaging the bot:

- `/set_review_group @yourgroup` or `/set_review_group -1001234567890`
- `/set_main_channel @yourchannel` or `/set_main_channel -1001234567890`
- `/show_targets` to check the saved IDs

Public `https://t.me/username` links are also accepted. Add the bot to the review group first; make it an administrator of the main channel with permission to post. The commands resolve the username/link to a numeric chat ID and save it in the app database, so changes persist across restarts when the database itself is stored persistently. Only the configured owner can change these destinations. Private invite links are not supported; use the numeric chat ID for private groups/channels.
