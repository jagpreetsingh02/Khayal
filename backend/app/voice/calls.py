"""Red-alert phone call to a family member (brief §5.8), behind CALL_PROVIDER.

Triggered only by a RED alert on a confirmed reading (the MCP server decides that and puts a
`voice_call` message in the outbox); amber alerts and missed doses never ring anyone.

- MockCallProvider (default): an "Incoming call" card in the child's simulator pane, with the
  TTS audio (or the browser's voice in mock voice mode).
- TwilioCallProvider (CALL_PROVIDER=twilio, untested live): an outbound call that plays the
  TTS audio (needs PUBLIC_BASE_URL) or reads the script with <Say>. It only ever calls members
  registered on the Twilio channel, never the simulator's demo numbers.
"""

import logging
import re
from typing import Protocol
from xml.sax.saxutils import escape

import httpx

from app.conversation.messages import Voice
from app.storage.models import Member

log = logging.getLogger(__name__)

TWILIO_API = "https://api.twilio.com/2010-04-01"
E164 = re.compile(r"^\+\d{8,15}$")
SAY_LANG = {"hi": "hi-IN", "ta": "ta-IN", "en": "en-IN"}


class CallProvider(Protocol):
    name: str

    def call(self, member: Member, script: Voice, audio_url: str | None, mock_voice: bool) -> bool:
        """Place the call; True if it was placed (or shown, in mock)."""
        ...


class MockCallProvider:
    name = "mock"

    def __init__(self, simulator) -> None:
        self.simulator = simulator

    def call(self, member: Member, script: Voice, audio_url: str | None, mock_voice: bool) -> bool:
        if member.channel != "simulator":
            log.info("mock call to %s (not on the simulator): not shown", member.id)
            return False
        self.simulator.call_card(
            member.channel_ref, script.text, lang=script.lang, audio_url=audio_url, mock=mock_voice
        )
        return True


class TwilioCallProvider:
    name = "twilio"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        *,
        public_base_url: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        self._sid = account_sid
        self._token = auth_token
        self.from_number = from_number
        self.public_base_url = public_base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30)

    def __repr__(self) -> str:
        return "TwilioCallProvider()"

    def twiml(self, script: Voice, audio_url: str | None) -> str:
        if audio_url and self.public_base_url:
            return f"<Response><Play>{escape(self.public_base_url + audio_url)}</Play></Response>"
        lang = SAY_LANG.get(script.lang, "en-IN")
        return f'<Response><Say language="{lang}">{escape(script.text)}</Say></Response>'

    def call(self, member: Member, script: Voice, audio_url: str | None, mock_voice: bool) -> bool:
        number = member.channel_ref.removeprefix("whatsapp:")
        if member.channel != "twilio" or not E164.match(number):
            log.info("no Twilio call for %s: not a registered Twilio number", member.id)
            return False
        try:
            resp = self._client.post(
                f"{TWILIO_API}/Accounts/{self._sid}/Calls.json",
                auth=(self._sid, self._token),
                data={
                    "To": number,
                    "From": self.from_number,
                    "Twiml": self.twiml(script, audio_url),
                },
            )
        except httpx.HTTPError as exc:
            log.warning("Twilio call failed: %s", type(exc).__name__)
            return False
        if resp.status_code not in (200, 201):
            log.warning("Twilio call HTTP %s", resp.status_code)
            return False
        return True


def get_call_provider(settings, simulator) -> CallProvider | None:
    if settings.call_provider == "mock":
        return MockCallProvider(simulator)
    if settings.call_provider == "twilio":
        if settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_call_from:
            return TwilioCallProvider(
                settings.twilio_account_sid,
                settings.twilio_auth_token,
                settings.twilio_call_from,
                public_base_url=settings.public_base_url,
            )
        log.info("CALL_PROVIDER=twilio without credentials / TWILIO_CALL_FROM: no calls")
    return None
