"""Register a Telegram user or WhatsApp number as a member of the demo family.

The sender mapping is server-side: a message is only accepted from a (channel, address)
pair stored in `members`. Examples (run from backend/, servers may be running):

    # your Telegram id: send /id to the bot
    uv run python ../scripts/add_member.py --channel telegram --ref 123456789 \\
        --role parent --name "Kamla (Telegram)"
    uv run python ../scripts/add_member.py --channel twilio --ref +919812345678 \\
        --role child --name "Ravi (WhatsApp)"

New members join the family of the simulator's demo parent (created if missing). A new
parent starts with consent pending, so their first message gets the consent notice.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.demo import ensure_demo_family  # noqa: E402
from app.storage import get_store  # noqa: E402
from app.storage.base import Store  # noqa: E402
from app.storage.models import Member  # noqa: E402


def add_member(store: Store, channel: str, ref: str, role: str, name: str) -> Member:
    ref = ref.removeprefix("whatsapp:").strip()
    if store.find_member_by_channel(channel, ref):
        raise ValueError(f"{channel} {ref} is already registered")
    family_id = ensure_demo_family(store).family_id
    return store.add_member(
        Member(
            family_id=family_id,
            role=role,
            name=name,
            channel=channel,
            channel_ref=ref,
            consent_status="pending" if role == "parent" else "granted",
        )
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--channel", choices=["telegram", "twilio", "simulator"], required=True)
    ap.add_argument("--ref", required=True, help="Telegram user id or +91... number")
    ap.add_argument("--role", choices=["parent", "child"], required=True)
    ap.add_argument("--name", required=True)
    args = ap.parse_args(argv)
    try:
        m = add_member(get_store(), args.channel, args.ref, args.role, args.name)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Added {m.role} {m.name} on {m.channel} ({m.channel_ref}), id {m.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
