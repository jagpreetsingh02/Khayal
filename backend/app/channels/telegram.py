"""Telegram channel (CHANNEL=telegram, TELEGRAM_BOT_TOKEN).

Local dev uses long polling (getUpdates), so no public URL is needed. A webhook
(POST /webhooks/telegram with TELEGRAM_WEBHOOK_SECRET) is optional.
Sender mapping: the Telegram user id is the member's `channel_ref` (channel "telegram");
`scripts/add_member.py` registers one. Sending "/id" to the bot replies with your id.
Private chats only. Photos come via getFile (Telegram allows up to 20 MB).

The bot token is part of every API URL, so URLs are never logged or put in errors.
"""

import asyncio
import logging
from typing import Any

import httpx

from app.channels.base import Channel, ChannelError
from app.conversation.messages import Incoming, Outgoing
from app.reader.schema import ImageInput

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines include the token URL

API = "https://api.telegram.org"
MAX_PHOTO_BYTES = 20 * 1024 * 1024


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, token: str, client: httpx.Client | None = None) -> None:
        if not token:
            raise ChannelError("CHANNEL=telegram needs TELEGRAM_BOT_TOKEN in .env")
        self._token = token
        self._client = client or httpx.Client(timeout=40)
        self.offset: int | None = None

    def _api(self, method: str, **params: Any) -> Any:
        try:
            r = self._client.post(f"{API}/bot{self._token}/{method}", json=params)
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:  # no URL in the message: it has the token
            raise ChannelError(f"Telegram {method} failed: {type(exc).__name__}") from None
        if not data.get("ok"):
            raise ChannelError(
                f"Telegram {method} failed: {data.get('description', r.status_code)}"
            )
        return data["result"]

    def _download(self, file_id: str) -> bytes:
        info = self._api("getFile", file_id=file_id)
        if (info.get("file_size") or 0) > MAX_PHOTO_BYTES or not info.get("file_path"):
            raise ChannelError("Telegram photo is too large or not downloadable")
        try:
            r = self._client.get(f"{API}/file/bot{self._token}/{info['file_path']}")
        except httpx.HTTPError:
            raise ChannelError("Telegram photo download failed") from None
        if r.status_code != 200:
            raise ChannelError(f"Telegram photo download failed: HTTP {r.status_code}")
        return r.content

    def receive(self, update: dict[str, Any]) -> Incoming | None:
        msg = update.get("message")
        if not msg or msg.get("chat", {}).get("type") != "private" or "from" not in msg:
            return None  # edits, channels, groups: ignored
        sender = str(msg["from"]["id"])
        text = msg.get("text") or msg.get("caption")
        if text and text.strip() in ("/id", "/start id"):
            self._api("sendMessage", chat_id=msg["chat"]["id"], text=f"Your Telegram id: {sender}")
            return None
        if text and text.startswith("/start"):
            text = "hello"  # Telegram's first message; the engine answers with consent/help
        image = None
        file_id = None
        if msg.get("photo"):
            file_id = max(msg["photo"], key=lambda p: p.get("width", 0) * p.get("height", 0))[
                "file_id"
            ]
        elif str(msg.get("document", {}).get("mime_type", "")).startswith("image/"):
            file_id = msg["document"]["file_id"]  # photo sent "as a file"
        if file_id:
            image = ImageInput(data=self._download(file_id), filename=None)
        if not text and image is None:
            return None  # stickers, voice notes (Phase 5), locations...
        return Incoming(channel="telegram", sender_ref=sender, text=text, image=image)

    def send(self, msg: Outgoing) -> None:
        # In a private chat the chat id equals the user id we store as channel_ref.
        self._api("sendMessage", chat_id=msg.to_ref, text=msg.text)

    # --- long polling (local dev) ---

    def poll_once(self, service, timeout: int = 25) -> int:
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if self.offset is not None:
            params["offset"] = self.offset
        updates = self._api("getUpdates", **params)
        for update in updates:
            self.offset = update["update_id"] + 1
            try:
                service.handle("telegram", update)
            except Exception:
                log.exception("telegram update %s failed", update.get("update_id"))
        return len(updates)

    async def start_polling(self, service) -> None:
        """Background task started by the API when CHANNEL=telegram (polling mode)."""
        await asyncio.to_thread(self._api, "deleteWebhook")  # getUpdates refuses while one is set
        log.info("Telegram long polling started")
        while True:
            try:
                await asyncio.to_thread(self.poll_once, service)
            except ChannelError as exc:
                log.warning("%s; retrying in 5 s", exc)
                await asyncio.sleep(5)
