"""Try the parent conversation in the terminal (the web simulator is at /simulator).

Uses a throwaway in-memory database with one parent (Kamla, consent not yet given) and two
children, so it never touches your local DB. Because the DB lives in this process, the MCP
server runs in-process here too; the API uses the real MCP server over HTTP.

    cd backend
    uv run python ../scripts/chat_demo.py              # interactive, mock reader
    uv run python ../scripts/chat_demo.py --demo       # scripted golden path
    uv run python ../scripts/chat_demo.py --reader groq   # real vision model (GROQ_API_KEY)

Commands: /photo <file in samples/ or any path> [caption]   send a photo
          /wait <minutes>                                   move the clock forward
                                                            (runs the timeouts)
          /quit
Anything else is sent as a text message.
"""

import argparse
import shlex
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agent.rule_agent import RuleAgent  # noqa: E402
from app.alerts.notifier import StoreNotifier  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.conversation.engine import ConversationEngine  # noqa: E402
from app.conversation.messages import Incoming, Outgoing  # noqa: E402
from app.reader import get_reader  # noqa: E402
from app.reader.labels import SAMPLES_DIR  # noqa: E402
from app.reader.schema import ImageInput  # noqa: E402
from app.storage.models import Family, Member, utcnow  # noqa: E402
from app.storage.sqlite_store import SQLiteStore  # noqa: E402
from mcp_server.server import create_server  # noqa: E402

PARENT_REF = "+919800000001"
DEMO_SCRIPT = [
    "hello",
    "haan",
    "/photo synthetic_bp_normal.jpg",
    "1",
    "/photo synthetic_bp_high.jpg",
    "1",
    "/wait 2",
    "/photo synthetic_bp_high.jpg",
    "/wait 4",
    "/photo synthetic_bp_high.jpg",
    "1",
    "/wait 61",
    "/photo synthetic_bp_high.jpg",
    "1",
    "/wait 16",
    "/photo synthetic_strip_telma40.jpg",
    "1",
    "what should I eat for diabetes?",
]


class Chat:
    def __init__(self, reader_name: str) -> None:
        self.store = SQLiteStore(":memory:")
        fam = self.store.add_family(Family(name="Sharma"))
        people = [("parent", "Kamla", PARENT_REF), ("child", "Ravi", "+919800000002"),
                  ("child", "Priya", "+919800000003")]  # fmt: skip
        for role, name, ref in people:
            self.store.add_member(
                Member(family_id=fam.id, role=role, name=name, channel="simulator", channel_ref=ref)
            )
        self.parent = self.store.find_member_by_channel("simulator", PARENT_REF)
        self.now = utcnow()
        settings = get_settings().model_copy(update={"llm_provider": reader_name})
        clock = lambda: self.now  # noqa: E731
        server = create_server(self.store, StoreNotifier(lambda: self.store), clock=clock)
        agent = RuleAgent(self.store, server)
        self.engine = ConversationEngine(self.store, get_reader(settings), agent, clock=clock)
        self.names = {m.id: m.name for m in self.store.list_members(fam.id)}

    def run(self, line: str) -> list[Outgoing]:
        line = line.strip()
        image, text = None, line
        if line.startswith("/wait"):
            minutes = float(line.split()[1])
            self.now += timedelta(minutes=minutes)
            print(f"   (clock +{minutes:g} min)")
            out = self.engine.tick()
            self._print(out)
            return out
        if line.startswith("/photo"):
            parts = shlex.split(line)
            path = Path(parts[1])
            path = path if path.exists() else SAMPLES_DIR / parts[1]
            image = ImageInput(data=path.read_bytes(), filename=path.name)
            text = " ".join(parts[2:]) or None
        out = self.engine.handle_message(
            Incoming(channel="simulator", sender_ref=PARENT_REF, text=text, image=image)
        )
        self._print(out)
        return out

    def _print(self, out: list[Outgoing]) -> None:
        for o in out:
            who = "bot" if o.member_id == self.parent.id else f"to {self.names[o.member_id]}"
            print(f"   [{who}] {o.text}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Chat with Care Circle in the terminal.")
    ap.add_argument("--reader", choices=["mock", "groq"], default="mock")
    ap.add_argument("--demo", action="store_true", help="run the scripted golden path")
    args = ap.parse_args(argv)
    chat = Chat(args.reader)
    if args.demo:
        for line in DEMO_SCRIPT:
            print(f"Kamla> {line}")
            chat.run(line)
        return 0
    print(__doc__.split("Commands:")[1].strip(), "\n")
    while True:
        try:
            line = input("Kamla> ")
        except (EOFError, KeyboardInterrupt):
            return 0
        if line.strip() in ("/quit", "/exit"):
            return 0
        if line.strip():
            try:
                chat.run(line)
            except (FileNotFoundError, IndexError, ValueError) as exc:
                print(f"   (error: {exc})")


if __name__ == "__main__":
    sys.exit(main())
