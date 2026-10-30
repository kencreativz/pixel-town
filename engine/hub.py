import asyncio
import json


class Hub:
    """Per-world pub/sub. Subscribers receive JSON strings."""

    def __init__(self):
        self._subs = set()
        self._seq = 0

    @property
    def viewers(self):
        return len(self._subs)

    def subscribe(self):
        q = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q):
        self._subs.discard(q)

    def publish(self, event):
        self._seq += 1
        event = {"seq": self._seq, **event}
        payload = json.dumps(event)
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                self._subs.discard(q)  # slow client: drop it; EventSource will reconnect
        return event
