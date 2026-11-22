"""scripts/chat_demo.py: the scripted golden path runs end to end with the mock reader."""

import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
spec = importlib.util.spec_from_file_location("chat_demo", SCRIPTS / "chat_demo.py")
chat_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chat_demo)


def test_scripted_demo_reaches_red_alert_and_reminder(capsys):
    assert chat_demo.main(["--demo", "--reader", "mock"]) == 0
    out = capsys.readouterr().out
    assert "Reply 1 (Haan / Yes) to agree" in out
    assert "[to Ravi] RED alert for Kamla" in out
    assert out.index("[to Ravi] RED alert") < out.index("[to Ravi] AMBER alert for Kamla: BP")
    assert "No repeat reading was taken within 15 minutes" in out
    assert "Telma 40 = Telmisartan 40 mg. Remind at 8 PM?" in out
    assert out.rstrip().endswith("I can only help with readings and medicines.")


def test_wait_and_photo_commands():
    chat = chat_demo.Chat("mock")
    chat.run("haan")
    start = chat.now
    assert chat.run("/wait 5") == [] and (chat.now - start).total_seconds() == 300
    [reply] = chat.run("/photo synthetic_glucose_mgdl.jpg")
    assert reply.text.startswith("I read sugar 112 mg/dL")
