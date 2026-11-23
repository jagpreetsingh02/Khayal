"""Voice (brief §5.8): phrases (en/hi/ta), speech parsing, voice notes in and out, ElevenLabs
TTS/STT with mocked HTTP (cache, budget, fallback), and the red-alert call. No network."""

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from app import clock
from app.config import Settings, get_settings
from app.conversation import parse
from app.conversation.messages import AudioInput, Outgoing, Voice
from app.storage.models import Member
from app.voice import phrases
from app.voice.calls import MockCallProvider, TwilioCallProvider, get_call_provider
from app.voice.stt import ElevenLabsSTT, MockSTT, get_stt
from app.voice.tts import ElevenLabsTTS, MockTTS, budget_key, get_tts
from tests.test_scheduler import CHILD, PARENT, World, set_clock

FAKE_KEY = "test-key-not-real"


class Ev:
    def __init__(self, **kw):
        base = dict(
            sender_ref=PARENT,
            text=None,
            sample=None,
            image_b64=None,
            filename=None,
            client_id=None,
            audio_b64=None,
            audio_mime=None,
            voice_sample=None,
        )
        base.update(kw)
        self.__dict__.update(base)


@pytest.fixture
def w():
    world = World()
    yield world
    clock.set_offset(timedelta(0))
    world.store.close()


# ---------- phrases ----------


def test_phrases_in_three_languages_with_english_fallback():
    assert (
        phrases.say("dose_taken", "en", names="Telma 40").text == "Noted, Telma 40 taken. "
        "Thank you."
    )
    hi = phrases.say(
        "dose_reminder", "hi", names="Telma 40", time=phrases.time_words("20:00", "hi")
    )
    assert hi.lang == "hi" and hi.text.startswith("रात 8 बजे: Telma 40 लेने का समय")
    ta = phrases.say("dose_followup", "ta", names="Telma 40", time="20:00")
    assert ta.lang == "ta" and "எடுத்துக்கொண்டீர்களா" in ta.text
    fallback = phrases.say("refill_approved", "ta", brand="Telma 40")  # no Tamil yet
    assert fallback.lang == "en" and "approved" in fallback.text
    assert phrases.say("off_topic", "fr").lang == "en"


def test_time_words():
    assert phrases.time_words("08:30", "en") == "8:30 AM"
    assert phrases.time_words("14:00", "hi") == "दोपहर 2 बजे"
    assert phrases.time_words("21:30", "hi") == "रात 9:30 बजे"
    assert phrases.time_words("07:00", "hi") == "सुबह 7 बजे"


# ---------- speech -> reply forms ----------


@pytest.mark.parametrize(
    ("said", "form"),
    [
        ("My BP is 142 by 88", "142/88"),
        ("BP 142 over 88, pulse 76", "142/88 76"),
        ("मेरा बीपी 186 बटा 121 है", "186/121"),
        ("बीपी १४२ ९०", "142/90"),
        ("Sugar 110 hai", "sugar 110"),
        ("मेरी शुगर 98 है", "sugar 98"),
        ("I took my medicine", "taken"),
        ("मैंने दवा ले ली", "taken"),
        ("maine dawai le li", "taken"),
        ("मैंने दवा नहीं ली", "2"),
        ("I missed it today", "2"),
        ("हाँ, सही है", "1"),
        ("Yes.", "1"),
        ("नहीं", "2"),
        ("stop", "stop"),
        ("What is the weather today?", "what is the weather today"),
    ],
)
def test_normalize_speech(said, form):
    assert parse.normalize_speech(said) == form


# ---------- voice notes in (STT) ----------


def test_spoken_reading_still_gets_the_read_back(w):
    w.svc.handle("simulator", Ev(voice_sample="bp_reading_en"))
    reply = w.feed(PARENT)[-1]
    assert reply.text.startswith("I read 142/88") and "correct?" in reply.text
    assert reply.voice and "ऊपर 142, नीचे 88" in reply.voice_text  # Kamla speaks Hindi
    [reading] = w.store.list_readings(w.parent.id)
    assert reading.status == "unconfirmed" and reading.source == "voice"
    voice_in = [i for i in w.sim.feed(PARENT) if i.direction == "in"][-1]
    assert voice_in.voice and voice_in.transcript == "My BP is 142 by 88"
    w.svc.handle("simulator", Ev(voice_sample="yes_hi"))
    assert w.store.get_reading(reading.id).status == "confirmed"


def test_spoken_taken_after_reminder(w):
    set_clock("08:00")
    w.run()
    set_clock("08:05")
    w.svc.handle("simulator", Ev(voice_sample="took_medicine_hi"))
    reply = w.feed(PARENT)[-1]
    assert reply.text == "Noted: Glycomet 500 and Telma 40 taken. Thank you."
    assert reply.voice_text == "ठीक है, Glycomet 500 and Telma 40 ले ली। धन्यवाद।"
    events = w.store.list_dose_events(w.parent.id, since=set_clock("00:00"))
    assert {e.status for e in events} == {"taken"}


def test_off_topic_voice_note_gets_the_fixed_refusal(w):
    w.svc.handle("simulator", Ev(voice_sample="weather_en"))
    reply = w.feed(PARENT)[-1]
    assert reply.text == "I can only help with readings and medicines."
    assert reply.voice_text == "मैं सिर्फ़ रीडिंग और दवाइयों में मदद कर सकता हूँ।"


def test_real_recording_in_mock_mode_is_not_understood(w):
    import base64

    w.svc.handle(
        "simulator", Ev(audio_b64=base64.b64encode(b"webm-bytes").decode(), audio_mime="audio/webm")
    )
    assert w.feed(PARENT)[-1].text.startswith("Sorry, I could not understand the voice note")
    assert w.store.list_readings(w.parent.id) == []


def test_text_replies_have_no_voice_note(w):
    w.say("142/88")
    assert w.feed(PARENT)[-1].voice is False  # typed in, text out


# ---------- voice notes out (TTS) ----------


def test_voice_off_means_text_only(w):
    w.svc.tts = None
    set_clock("08:00")
    w.run()
    reminder = w.feed(PARENT)[-1]
    assert reminder.voice is False and reminder.voice_text is None


class FakeEleven:
    def __init__(self, *responses: httpx.Response):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def tts(fake, store, tmp_path: Path, budget=1000) -> ElevenLabsTTS:
    return ElevenLabsTTS(
        FAKE_KEY,
        "voice123",
        model="eleven_v4_turbo",
        cache_dir=tmp_path,
        store=store,
        daily_char_budget=budget,
        client=fake.client(),
    )


def test_elevenlabs_tts_request_and_cache(w, tmp_path):
    fake = FakeEleven(httpx.Response(200, content=b"ID3-mp3-bytes"))
    engine = tts(fake, w.store, tmp_path)
    voice = Voice(text="रात 8 बजे: Telma 40 लेने का समय हो गया है।", lang="hi")
    url = engine.speak(voice)
    assert url and url.startswith("/voice/") and url.endswith(".mp3")
    req = fake.requests[0]
    assert req.url.path == "/v1/text-to-speech/voice123"
    assert req.url.params["output_format"] == "mp3_44100_64"
    assert req.headers["xi-api-key"] == FAKE_KEY
    body = json.loads(req.content)
    assert body == {"text": voice.text, "model_id": "eleven_v4_turbo", "language_code": "hi"}
    assert (tmp_path / url.removeprefix("/voice/")).read_bytes() == b"ID3-mp3-bytes"
    assert engine.speak(voice) == url and len(fake.requests) == 1  # cached: no 2nd call
    assert repr(engine) == "ElevenLabsTTS(model='eleven_v4_turbo')"  # key never shown


def test_elevenlabs_tts_daily_budget_falls_back_to_text(w, tmp_path):
    fake = FakeEleven(httpx.Response(200, content=b"mp3"))
    engine = tts(fake, w.store, tmp_path, budget=40)
    assert engine.speak(Voice(text="x" * 30, lang="en"))
    assert engine.speak(Voice(text="y" * 30, lang="en")) is None  # 60 > 40
    assert len(fake.requests) == 1
    assert int(w.store.get_kv(budget_key(_today()))) == 30  # cache misses only


def _today() -> str:
    from zoneinfo import ZoneInfo

    from app.storage.models import utcnow

    return utcnow().astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()


def test_elevenlabs_tts_error_is_text_only(w, tmp_path):
    fake = FakeEleven(httpx.Response(401, json={"detail": "invalid key"}))
    assert tts(fake, w.store, tmp_path).speak(Voice(text="hello", lang="en")) is None


def test_failed_real_tts_drops_the_voice_note(w, tmp_path):
    w.svc.tts = tts(FakeEleven(httpx.Response(500)), w.store, tmp_path)
    w.svc.deliver(
        [
            Outgoing(
                channel="simulator",
                to_ref=PARENT,
                member_id=w.parent.id,
                text="Saved.",
                voice=Voice(text="Saved.", lang="en"),
            )
        ]
    )
    item = w.feed(PARENT)[-1]
    assert item.text == "Saved." and item.voice is False


def test_real_tts_audio_reaches_the_simulator(w, tmp_path):
    w.svc.tts = tts(FakeEleven(httpx.Response(200, content=b"mp3")), w.store, tmp_path)
    set_clock("08:00")
    w.run()
    item = w.feed(PARENT)[-1]
    assert item.voice and item.audio_url.startswith("/voice/") and item.voice_mock is False


def test_provider_selection_needs_keys():
    base = dict(_env_file=None)
    assert get_tts(Settings(**base, voice="none"), None) is None
    assert isinstance(get_tts(Settings(**base, voice="mock"), None), MockTTS)
    assert get_tts(Settings(**base, voice="elevenlabs"), None) is None  # no key: text only
    assert isinstance(get_stt(Settings(**base, voice="elevenlabs")), MockSTT)
    assert get_stt(Settings(**base, voice="none")) is None
    with_key = Settings(
        **base, voice="elevenlabs", elevenlabs_api_key=FAKE_KEY, elevenlabs_voice_id="v"
    )
    assert isinstance(get_tts(with_key, None), ElevenLabsTTS)
    assert isinstance(get_stt(with_key), ElevenLabsSTT)
    assert get_call_provider(Settings(**base, call_provider="twilio"), None) is None  # no creds


# ---------- STT ----------


def test_elevenlabs_stt_request_shape():
    # Shape of a real scribe_v3 reply (live check 23 Nov 2026): 3-letter language codes.
    reply = {"text": "मैंने दवा ले ली", "language_code": "hin", "language_probability": 0.99}
    fake = FakeEleven(httpx.Response(200, json=reply))
    stt = ElevenLabsSTT(FAKE_KEY, model="scribe_v3", client=fake.client())
    text = stt.transcribe(AudioInput(data=b"\x1aE\xdf\xa3webm", mime="audio/webm"))
    assert text == "मैंने दवा ले ली"
    req = fake.requests[0]
    assert req.url.path == "/v1/speech-to-text" and req.headers["xi-api-key"] == FAKE_KEY
    body = req.content.decode("latin-1")
    assert 'name="model_id"' in body and "scribe_v3" in body and 'name="file"' in body
    assert 'name="language_code"' not in body  # auto-detect, so Hinglish works


def test_elevenlabs_stt_errors_and_limits():
    fake = FakeEleven(httpx.Response(500))
    stt = ElevenLabsSTT(FAKE_KEY, model="scribe_v3", client=fake.client())
    assert stt.transcribe(AudioInput(data=b"x", mime="audio/webm")) is None
    assert stt.transcribe(AudioInput(data=b"")) is None
    assert stt.transcribe(AudioInput(data=b"x" * (5 * 1024 * 1024 + 1))) is None
    assert len(fake.requests) == 1


# ---------- red-alert call ----------


@pytest.fixture
def calls_on(monkeypatch):
    monkeypatch.setenv("CALL_PROVIDER", "mock")
    get_settings.cache_clear()
    yield
    monkeypatch.setenv("CALL_PROVIDER", "none")
    get_settings.cache_clear()


def _confirmed_bp(w, sys_, dia, minutes_ago):
    from app.storage.models import Reading, utcnow

    r = w.store.add_reading(
        Reading(
            parent_id=w.parent.id,
            type="bp",
            systolic=sys_,
            diastolic=dia,
            created_at=utcnow() - timedelta(minutes=minutes_ago),
        )
    )
    return w.store.set_reading_status(r.id, "confirmed")


def test_confirmed_red_reading_rings_the_family(w, calls_on):
    set_clock("10:00")
    _confirmed_bp(w, 188, 122, 10)
    repeat = _confirmed_bp(w, 190, 124, 0)
    w.svc.agent.after_confirmed_reading(w.parent, repeat)
    w.svc.flush_outbox()
    [card] = [i for i in w.feed(CHILD) if i.kind == "voice_call"]
    assert card.voice and card.voice_mock and card.voice_lang == "en"
    assert card.text.startswith("This is Khayal with an urgent alert about Kamla.")
    assert "190/124" in card.text and card.text.endswith("Please call Kamla now.")
    assert "If you feel unwell" not in card.text  # parent wording stays out of the call
    assert any(t.startswith("RED alert") for t in w.texts(CHILD))


def test_amber_and_missed_doses_never_ring(w, calls_on):
    set_clock("08:00")
    w.run()
    set_clock("08:30")
    w.run()  # 2 missed -> amber
    assert any(t.startswith("AMBER") for t in w.texts(CHILD))
    assert not any(i.kind == "voice_call" for i in w.feed(CHILD))


def test_no_call_provider_no_card(w, calls_on):
    w.svc.calls = None
    set_clock("10:00")
    _confirmed_bp(w, 188, 122, 10)
    w.svc.agent.after_confirmed_reading(w.parent, _confirmed_bp(w, 190, 124, 0))
    w.svc.flush_outbox()
    assert not any(i.kind == "voice_call" for i in w.feed(CHILD))
    assert any(t.startswith("RED alert") for t in w.texts(CHILD))  # the message still goes


def test_mock_call_only_on_simulator_members():
    shown = []

    class Sim:
        def call_card(self, *a, **k):
            shown.append(a)

    m = Member(family_id="f", role="child", name="R", channel="telegram", channel_ref="1")
    assert MockCallProvider(Sim()).call(m, Voice(text="x"), None, True) is False and shown == []


def test_twilio_call_only_dials_registered_twilio_numbers():
    fake = FakeEleven(httpx.Response(201, json={"sid": "CA1"}))
    provider = TwilioCallProvider(
        "AC1", "tok", "+14155550100", public_base_url="https://demo.example", client=fake.client()
    )
    sim_member = Member(
        family_id="f", role="child", name="R", channel="simulator", channel_ref="+919800000002"
    )
    assert provider.call(sim_member, Voice(text="x"), None, False) is False  # demo number
    real = Member(
        family_id="f",
        role="child",
        name="R",
        channel="twilio",
        channel_ref="whatsapp:+919811112222",
    )
    assert provider.call(real, Voice(text="Call Kamla now", lang="hi"), "/voice/abc.mp3", False)
    form = dict(httpx.QueryParams(fake.requests[0].content.decode()))
    assert form["To"] == "+919811112222" and form["From"] == "+14155550100"
    assert form["Twiml"] == "<Response><Play>https://demo.example/voice/abc.mp3</Play></Response>"
    assert provider.twiml(Voice(text="a & b", lang="hi"), None) == (
        '<Response><Say language="hi-IN">a &amp; b</Say></Response>'
    )
