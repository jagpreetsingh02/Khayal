"""Per-member turns: work for one member runs strictly in arrival order, work for different
members runs in parallel.

A message takes a ticket when it is accepted (`reserve`, cheap and non-blocking) and waits
for its turn when it is processed. Tickets are served in the order they were handed out, so
two messages from the same parent are handled in the order they arrived, even if a later
one reaches a worker thread first. (A plain `threading.Lock` is not FIFO.)

Nothing here is shared between members: a slow photo (vision call) or voice note (STT) for
one parent only holds that parent's turn.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class Ticket:
    key: str
    number: int


class Turns:
    def __init__(self) -> None:
        self._cv = threading.Condition()
        # key -> [next ticket number, number now being served]; removed when nobody waits
        self._lines: dict[str, list[int]] = {}

    def reserve(self, key: str) -> Ticket:
        """A place in this key's line. Every reserved ticket must be used with `turn`."""
        with self._cv:
            line = self._lines.setdefault(key, [0, 0])
            ticket = Ticket(key, line[0])
            line[0] += 1
        return ticket

    @contextmanager
    def turn(self, ticket: Ticket) -> Iterator[None]:
        """Wait until every earlier ticket of the same key has finished, then run."""
        with self._cv:
            self._cv.wait_for(lambda: self._lines[ticket.key][1] == ticket.number)
        try:
            yield
        finally:
            self._release(ticket.key)

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        """Join the line and wait (demo buttons, explicit per-member actions)."""
        with self.turn(self.reserve(key)):
            yield

    @contextmanager
    def try_hold(self, key: str) -> Iterator[bool]:
        """Run now only if nobody holds or waits for this key; yields False otherwise.
        The background ticker uses it so one busy parent never delays the others."""
        with self._cv:
            got = key not in self._lines
            if got:
                self._lines[key] = [1, 0]
        try:
            yield got
        finally:
            if got:
                self._release(key)

    def busy(self) -> bool:
        with self._cv:
            return bool(self._lines)

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until no work is running or waiting (tests, shutdown)."""
        with self._cv:
            return self._cv.wait_for(lambda: not self._lines, timeout)

    def _release(self, key: str) -> None:
        with self._cv:
            line = self._lines[key]
            line[1] += 1
            if line[1] == line[0]:
                del self._lines[key]
            self._cv.notify_all()
