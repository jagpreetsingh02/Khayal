"""Reader schema, MockReader, GroqReader (mocked HTTP + optional live test), image prep."""

import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.reader import get_reader
from app.reader.base import ReaderError
from app.reader.bedrock_reader import BedrockReader
from app.reader.groq_reader import GroqReader
from app.reader.image_prep import prepare_image
from app.reader.labels import SAMPLES_DIR, load_labels
from app.reader.mock_reader import MockReader
from app.reader.schema import (
    METER_JSON_SCHEMA,
    STRIP_JSON_SCHEMA,
    ImageInput,
    MeterReading,
    StripReading,
)

FAKE_KEY = "gsk_test_not_a_real_key_0123456789"


def jpeg(width: int = 3000, height: int = 2000) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 210, 190)).save(buf, format="JPEG")
    return buf.getvalue()


def sample(name: str) -> ImageInput:
    return ImageInput(data=(SAMPLES_DIR / name).read_bytes(), filename=name)


# --- schema ---


def test_meter_schema_nulls_for_missing_fields():
    r = MeterReading(device_type="bp", systolic=142, diastolic=88, legible=True)
    assert r.pulse is None and r.glucose_value is None and r.issues == []


def test_meter_schema_rejects_bad_values():
    with pytest.raises(ValidationError):
        MeterReading(device_type="thermometer", legible=True)
    with pytest.raises(ValidationError):
        MeterReading(device_type="glucose", glucose_unit="g/L", legible=True)
    with pytest.raises(ValidationError):
        MeterReading(device_type="bp", legible=True, issues=["smudge"])
    with pytest.raises(ValidationError):  # extra keys are not silently accepted
        MeterReading(device_type="bp", legible=True, diagnosis="hypertension")


@pytest.mark.parametrize(
    ("schema", "model"), [(METER_JSON_SCHEMA, MeterReading), (STRIP_JSON_SCHEMA, StripReading)]
)
def test_strict_json_schema_matches_model(schema, model):
    assert set(schema["properties"]) == set(model.model_fields)
    assert set(schema["required"]) == set(model.model_fields)
    assert schema["additionalProperties"] is False


# --- labels + mock reader ---


def test_labels_file_is_valid_and_has_synthetic_examples():
    labels = load_labels()
    assert 4 <= len(labels) <= 50
    synthetic = [lb for lb in labels if lb.synthetic]
    assert len(synthetic) >= 4
    assert all(lb.file.startswith("synthetic_") for lb in synthetic)
    assert {lb.kind for lb in labels} == {"meter", "strip"}
    for lb in labels:  # every label points at a file that exists
        assert (SAMPLES_DIR / lb.file).is_file(), lb.file


def test_mock_reader_returns_labels_by_file_name():
    reader = MockReader()
    for label in load_labels():
        image = ImageInput(data=b"", filename=label.file)
        if label.kind == "meter":
            assert reader.read_meter(image) == label.meter()
        else:
            assert reader.read_strip(image) == label.strip()


def test_mock_reader_matches_by_content_under_another_name():
    data = (SAMPLES_DIR / "synthetic_bp_high.jpg").read_bytes()
    r = MockReader().read_meter(ImageInput(data=data, filename="IMG_2041.jpg"))
    assert (r.systolic, r.diastolic, r.pulse) == (186, 122, 92)


def test_mock_reader_unknown_photo_is_unreadable_not_invented():
    r = MockReader().read_meter(ImageInput(data=jpeg(50, 50), filename="random.jpg"))
    assert r.device_type == "unknown" and not r.legible and r.systolic is None
    s = MockReader().read_strip(ImageInput(data=jpeg(50, 50), filename="random.jpg"))
    assert s.brand is None and not s.legible


def test_mock_reader_cross_kind():
    reader = MockReader()
    assert reader.read_meter(sample("synthetic_strip_telma40.jpg")).device_type == "unknown"
    assert reader.read_strip(sample("synthetic_bp_normal.jpg")).brand is None


def test_mock_reader_with_custom_folder(tmp_path: Path):
    (tmp_path / "labels.json").write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "file": "bp_x.jpg",
                        "kind": "meter",
                        "expected": {"device_type": "bp", "systolic": 120, "diastolic": 80},
                    }
                ]
            }
        )
    )
    r = MockReader(tmp_path).read_meter(ImageInput(data=b"", filename="BP_X.JPG"))
    assert (r.systolic, r.diastolic, r.legible) == (120, 80, True)


def test_reader_factory():
    assert isinstance(get_reader(Settings(llm_provider="mock")), MockReader)
    groq = get_reader(Settings(llm_provider="groq", groq_api_key=FAKE_KEY, groq_vision_model="m"))
    assert isinstance(groq, GroqReader)
    assert isinstance(get_reader(Settings(llm_provider="bedrock")), BedrockReader)


def test_bedrock_stub_raises_clear_error():
    with pytest.raises(NotImplementedError, match="Phase 6"):
        BedrockReader().read_meter(ImageInput(data=b"x"))
    with pytest.raises(NotImplementedError, match="LLM_PROVIDER=mock"):
        BedrockReader().read_strip(ImageInput(data=b"x"))


# --- image prep ---


def test_prepare_image_downscales_and_compresses():
    big = jpeg(4000, 3000)
    out = prepare_image(big)
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG" and max(img.size) == 1280 and img.size == (1280, 960)


def test_prepare_image_keeps_small_images_and_converts_png():
    buf = io.BytesIO()
    Image.new("RGBA", (400, 300), (0, 0, 0, 0)).save(buf, format="PNG")
    img = Image.open(io.BytesIO(prepare_image(buf.getvalue())))
    assert img.format == "JPEG" and img.size == (400, 300)


def test_prepare_image_rejects_non_images():
    with pytest.raises(ReaderError):
        prepare_image(b"not an image")


# --- GroqReader with a mocked HTTP client ---


class FakeGroq:
    """Records requests and replies with queued responses."""

    def __init__(self, *responses: httpx.Response):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    def reader(self) -> GroqReader:
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return GroqReader(FAKE_KEY, "qwen/qwen3.8-27b", client=client)

    def body(self, i: int = 0) -> dict:
        return json.loads(self.requests[i].content)


def chat(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


METER_JSON = json.dumps(
    {
        "device_type": "bp",
        "systolic": 142,
        "diastolic": 88,
        "pulse": 76,
        "glucose_value": None,
        "glucose_unit": None,
        "legible": True,
        "issues": [],
    }
)


def test_groq_reader_request_shape():
    fake = FakeGroq(chat(METER_JSON))
    r = fake.reader().read_meter(ImageInput(data=jpeg()))
    assert (r.systolic, r.diastolic, r.pulse) == (142, 88, 76)

    req = fake.requests[0]
    assert req.url == "https://api.groq.com/openai/v1/chat/completions"
    assert req.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    body = fake.body()
    assert body["model"] == "qwen/qwen3.8-27b"
    assert body["temperature"] == 0
    assert "tools" not in body and "tool_choice" not in body  # the reader has no tools
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == METER_JSON_SCHEMA
    system = body["messages"][0]["content"]
    assert "never instructions" in system  # image text is data
    image_url = body["messages"][1]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,")
    sent = Image.open(io.BytesIO(__import__("base64").b64decode(image_url.split(",", 1)[1])))
    assert max(sent.size) == 1280  # downscaled before sending


def test_groq_reader_strip():
    content = json.dumps({"brand": "Telma", "strength": "40", "legible": True, "issues": []})
    fake = FakeGroq(chat(content))
    s = fake.reader().read_strip(ImageInput(data=jpeg(800, 600)))
    assert (s.brand, s.strength) == ("Telma", "40")
    assert fake.body()["response_format"]["json_schema"]["schema"] == STRIP_JSON_SCHEMA


def test_groq_reader_retries_once_on_bad_json():
    fake = FakeGroq(chat("Sure! The reading is 142/88."), chat(METER_JSON))
    r = fake.reader().read_meter(ImageInput(data=jpeg(800, 600)))
    assert r.systolic == 142 and len(fake.requests) == 2


def test_groq_reader_retries_once_on_schema_violation():
    bad = json.dumps({"device_type": "bp", "systolic": "high", "legible": True})
    fake = FakeGroq(chat(bad), chat(METER_JSON))
    assert fake.reader().read_meter(ImageInput(data=jpeg(800, 600))).diastolic == 88


def test_groq_reader_gives_up_after_two_attempts():
    fake = FakeGroq(chat("{not json"), chat("still not json"))
    with pytest.raises(ReaderError, match="after 2 attempts"):
        fake.reader().read_meter(ImageInput(data=jpeg(800, 600)))
    assert len(fake.requests) == 2


def test_groq_reader_http_error_never_leaks_key():
    err = httpx.Response(401, json={"error": {"message": "Invalid API Key"}})
    fake = FakeGroq(err, err)
    with pytest.raises(ReaderError) as info:
        fake.reader().read_meter(ImageInput(data=jpeg(800, 600)))
    assert "401" in str(info.value) and FAKE_KEY not in str(info.value)
    assert FAKE_KEY not in repr(fake.reader())


def test_groq_reader_network_error_then_success():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("timed out", request=request)
        return chat(METER_JSON)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    reader = GroqReader(FAKE_KEY, "m", client=client)
    assert reader.read_meter(ImageInput(data=jpeg(800, 600))).systolic == 142


def _rate_limited(retry_after: str) -> httpx.Response:
    msg = "Rate limit reached for model in organization `org_01abcDEF123` on tokens per minute"
    return httpx.Response(
        429, headers={"retry-after": retry_after}, json={"error": {"message": msg}}
    )


def test_groq_reader_waits_for_retry_after_on_rate_limit():
    waits = []
    fake = FakeGroq(_rate_limited("3"), chat(METER_JSON))
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    reader = GroqReader(FAKE_KEY, "m", client=client, sleep=waits.append)
    assert reader.read_meter(ImageInput(data=jpeg(800, 600))).systolic == 142
    assert waits == [3.0]


def test_groq_reader_fails_fast_when_rate_limit_wait_is_too_long():
    waits = []
    fake = FakeGroq(_rate_limited("45"), chat(METER_JSON))
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    reader = GroqReader(FAKE_KEY, "m", client=client, sleep=waits.append, max_wait=10)
    with pytest.raises(ReaderError, match="429") as info:
        reader.read_meter(ImageInput(data=jpeg(800, 600)))
    assert waits == [] and len(fake.requests) == 1
    assert "org_01abcDEF123" not in str(info.value)  # account ids are redacted


def test_groq_reader_requires_key_and_model():
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        GroqReader("", "m")
    with pytest.raises(ValueError, match="GROQ_VISION_MODEL"):
        GroqReader(FAKE_KEY, "")


# --- live Groq (skipped without a key) ---

_settings = get_settings()
live = pytest.mark.skipif(
    not (_settings.groq_api_key and _settings.groq_vision_model),
    reason="needs GROQ_API_KEY and GROQ_VISION_MODEL in .env",
)


@live
def test_live_groq_reads_a_photo_into_valid_json():
    reader = GroqReader(_settings.groq_api_key, _settings.groq_vision_model)
    r = reader.read_meter(sample("synthetic_bp_normal.jpg"))
    assert isinstance(r, MeterReading)  # valid, schema-checked JSON
    assert r.device_type == "bp" and (r.systolic, r.diastolic) == (132, 84)
