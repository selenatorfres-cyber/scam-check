from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from telethon.tl import types

SCAM_PATTERNS = [
    r"\bscammer\b", r"\bscam\b", r"\bfraud\b", r"marked\s+as\s+a\s+scammer\b",
    r"user\s+@?[A-Za-z0-9_]{3,32}", r"user\s+id", r"\bproof\b", r"\breport\b",
]
PROMO_PATTERNS = [
    r"\bjoin\b", r"join\s+our", r"\bsubscribe\b", r"advertisement", r"\bsponsored\b",
    r"\bpromotion\b", r"\bpromo\b", r"\breferral\b", r"referral\s+code", r"\baffiliate\b",
    r"use\s+my\s+code", r"\bcasino\b", r"\bsportsbook\b", r"\bgiveaway\b", r"\bairdrop\b",
    r"\bbuy\s+now\b", r"\bpartner\b", r"\bsponsor(?:ed)?\s+by\b", r"#1\s+(?:channel|service)",
]
PROOF_LABELS = ("view full report", "full report", "view report", "view proof", "view proofs", "proof", "evidence")


@dataclass
class ParsedReport:
    is_report: bool
    username: str | None = None
    user_id: int | None = None
    profile_url: str | None = None
    proof_url: str | None = None
    source_message_url: str | None = None
    confidence: float = 0.0


def _clean_username(value: str) -> str:
    return value.lstrip("@").strip()


def _is_valid_telegram_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in {"http", "https"} and p.netloc.lower() in {"t.me", "telegram.me"}
    except Exception:
        return False


def _message_url(message, channel_username: str | None = None) -> str | None:
    if channel_username:
        return f"https://t.me/{channel_username.lstrip('@')}/{message.id}"
    peer = getattr(message, "peer_id", None)
    channel_id = getattr(peer, "channel_id", None)
    if channel_id:
        return f"https://t.me/c/{channel_id}/{message.id}"
    return None


def _extract_button_links(message) -> list[tuple[str, str]]:
    out = []
    markup = getattr(message, "reply_markup", None)
    if not markup:
        return out
    for row in getattr(markup, "rows", []) or []:
        for button in getattr(row, "buttons", []) or []:
            text = (getattr(button, "text", "") or "").strip().lower()
            url = getattr(button, "url", None)
            if url and _is_valid_telegram_url(url):
                out.append((text, url))
    return out


def parse_message(message, channel_username: str | None = None) -> ParsedReport:
    text = message.raw_text or ""
    lower = text.lower()
    hits = sum(bool(re.search(pattern, lower, re.I)) for pattern in SCAM_PATTERNS)
    is_report = hits >= 1 and ("scammer" in lower or "marked as" in lower or "proof" in lower or "report" in lower)

    username = None
    user_id = None

    # Prefer a combined "User @name (123)" pattern.
    m = re.search(r"user\s+@?([A-Za-z0-9_]{3,32})\s*\(\s*(\d{3,20})\s*\)", text, re.I)
    if m:
        username = m.group(1)
        user_id = int(m.group(2))
    else:
        m = re.search(r"@([A-Za-z0-9_]{3,32})", text)
        if m:
            username = m.group(1)
        id_match = re.search(r"(?:user\s*id|id)\s*[:#]?\s*(\d{3,20})", text, re.I)
        if id_match:
            user_id = int(id_match.group(1))

    entities = getattr(message, "entities", None) or []
    for entity in entities:
        if isinstance(entity, types.MessageEntityTextUrl):
            url = entity.url
            if _is_valid_telegram_url(url) and re.search(r"t\.me/[A-Za-z0-9_]{3,32}$", url):
                if username is None:
                    username = url.rstrip("/").rsplit("/", 1)[-1]
                    break

    profile_url = f"https://t.me/{username}" if username else None
    proof_url = None

    # Text-link entities with proof-like labels have highest priority.
    for entity in entities:
        if isinstance(entity, types.MessageEntityTextUrl):
            label = text[entity.offset: entity.offset + entity.length].strip().lower()
            if any(x in label for x in PROOF_LABELS) and _is_valid_telegram_url(entity.url):
                proof_url = entity.url
                break

    # Then inspect inline buttons, prioritizing proof labels and avoiding obvious promotions.
    buttons = _extract_button_links(message)
    if not proof_url:
        for label, url in buttons:
            if any(x in label for x in PROOF_LABELS) and not any(re.search(p, label, re.I) for p in PROMO_PATTERNS):
                proof_url = url
                break

    if not proof_url:
        # Last resort: a Telegram message URL in text/entities. Do not use arbitrary external links.
        for url in re.findall(r"https?://(?:t\.me|telegram\.me)/[^\s)]+", text, re.I):
            url = url.rstrip(".,)")
            path = urlparse(url).path.strip("/")
            if "/" in path and _is_valid_telegram_url(url):
                proof_url = url
                break

    confidence = min(1.0, hits / 3.0 + (0.35 if username else 0) + (0.25 if user_id else 0) + (0.2 if proof_url else 0))
    return ParsedReport(
        is_report=is_report,
        username=username,
        user_id=user_id,
        profile_url=profile_url,
        proof_url=proof_url,
        source_message_url=_message_url(message, channel_username),
        confidence=confidence,
    )
