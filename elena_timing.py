"""One reply budget, measured from receipt rather than after model completion."""
import asyncio
import random
from time import monotonic
from dataclasses import dataclass

REPLY_BUDGET = 8.0
DELIVERY_RESERVE = 2.0


@dataclass(frozen=True)
class ReplyTiming:
    started: float
    delay: float

    @classmethod
    def start(cls):
        return cls(monotonic(), random.choice((4.0, 5.0, 6.0)))

    def remaining(self, *, reserve=0.0):
        return max(0.0, self.started + REPLY_BUDGET - reserve - monotonic())

    def processing_remaining(self):
        return self.remaining(reserve=DELIVERY_RESERVE)

    async def wait(self):
        # Model work and lock/lookup time already count toward the target.
        delay = min(self.processing_remaining(),
                    max(0.0, self.started + self.delay - monotonic()))
        if delay > 0:
            await asyncio.sleep(delay)
