"""scripts/add_member.py: registers channel senders into the demo family."""

import importlib.util
from pathlib import Path

import pytest

from app.demo import PARENT_REF
from app.storage.sqlite_store import SQLiteStore

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
spec = importlib.util.spec_from_file_location("add_member", SCRIPTS / "add_member.py")
add_member = importlib.util.module_from_spec(spec)
spec.loader.exec_module(add_member)


def test_adds_telegram_parent_and_whatsapp_child_to_the_demo_family():
    store = SQLiteStore(":memory:")
    p = add_member.add_member(store, "telegram", "123456", "parent", "Kamla TG")
    c = add_member.add_member(store, "twilio", "whatsapp:+919812345678", "child", "Ravi WA")
    demo = store.find_member_by_channel("simulator", PARENT_REF)
    assert p.family_id == c.family_id == demo.family_id
    assert p.consent_status == "pending" and c.channel_ref == "+919812345678"
    assert store.find_member_by_channel("telegram", "123456").id == p.id
    with pytest.raises(ValueError, match="already registered"):
        add_member.add_member(store, "telegram", "123456", "parent", "again")
