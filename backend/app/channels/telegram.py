"""Telegram channel (CHANNEL=telegram, TELEGRAM_BOT_TOKEN).

Local dev uses long polling (getUpdates), so no public URL is needed. A webhook
(POST /webhooks/telegram with TELEGRAM_WEBHOOK_SECRET) is optional.
Sender mapping: the Telegram user id is the member's `channel_ref` (channel "telegram");
`scripts/add_member.py` registers one. Sending "/id" to the bot replies with your id.
Private chats only. Photos come via getFile (Telegram allows up to 20 MB).
Duplicates: every update is written to the inbox with its update_id (webhook and polling);
a repeated one is skipped, so a webhook retry or a restart that re-reads updates never logs
a reading twice. The poller moves its offset past an update only after it is stored, so an
update that could not be stored is fetched again.

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

    def sender_of(self, update: dict[str, Any]) -> str | None:
        sender = (update.get("message") or {}).get("from", {}).get("id")
        return None if sender is None else str(sender)

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

    def fetch_updates(self, timeout: int = 25) -> list[dict[str, Any]]:
        """Updates after the current offset. The offset moves on only in `_stored`."""
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if self.offset is not None:
            params["offset"] = self.offset
        return self._api("getUpdates", **params)

    def _stored(self, update: dict[str, Any]) -> None:
        """This update is in the inbox: Telegram may forget it (next getUpdates)."""
        uid = update.get("update_id")
        if isinstance(uid, int):
            self.offset = max(self.offset or 0, uid + 1)

    def poll_once(self, service, timeout: int = 25) -> int:
        """Fetch updates, store them in the inbox and process them in this thread (tests,
        scripts)."""
        updates = self.fetch_updates(timeout)
        for update in updates:
            service.accept("telegram", update, update_id(update))
            self._stored(update)
        service.run_inbox()
        return len(updates)

    async def start_polling(self, service, inbound) -> None:
        """Background task started by the API when CHANNEL=telegram (polling mode). Each
        update is written to the inbox in order (`inbound`), then processed by its worker,
        so one parent's slow photo does not hold up the others."""
        await asyncio.to_thread(self._api, "deleteWebhook")  # getUpdates refuses while one is set
        log.info("Telegram long polling started")
        while True:
            try:
                updates = await asyncio.to_thread(self.fetch_updates)
            except ChannelError as exc:
                log.warning("%s; retrying in 5 s", exc)
                await asyncio.sleep(5)
                continue
            for update in updates:
                try:
                    await inbound.accept("telegram", update, update_id(update))
                except Exception:  # not stored: keep the offset, fetch it again
                    log.exception("could not store a Telegram update; fetching it again in 5 s")
                    await asyncio.sleep(5)
                    break
                self._stored(update)


def update_id(update: dict[str, Any]) -> str | None:
    uid = update.get("update_id")
    return None if uid is None else str(uid)
