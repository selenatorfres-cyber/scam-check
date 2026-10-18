from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    api_id: int = Field(alias="API_ID")
    api_hash: str = Field(alias="API_HASH")
    mtproto_session: str = Field(default="./data/sessions/account_b", alias="MTPROTO_SESSION")
    owner_id: int = Field(alias="OWNER_ID")
    admin_ids: str = Field(default="", alias="ADMIN_IDS")
    review_group_id: int = Field(default=0, alias="REVIEW_GROUP_ID")
    main_channel_id: int = Field(default=0, alias="MAIN_CHANNEL_ID")
    database_url: str = Field(default="sqlite+aiosqlite:///./data/app.db", alias="DATABASE_URL")
    default_logo: str = Field(default="./data/logos/default.png", alias="DEFAULT_LOGO")
    timezone: str = Field(default="Asia/Kolkata", alias="TIMEZONE")

    @property
    def admins(self) -> set[int]:
        ids = {self.owner_id}
        ids.update(int(x.strip()) for x in self.admin_ids.split(",") if x.strip())
        return ids


settings = Settings()
