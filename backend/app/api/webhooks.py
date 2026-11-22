"""Channel webhooks (Telegram optional webhook, Twilio WhatsApp). Filled in by the adapters."""

from fastapi import APIRouter

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
