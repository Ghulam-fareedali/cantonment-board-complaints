"""The worker: the beating heart that makes the orchestrator autonomous.

It blocks on two Redis queues and drives the orchestrator:
  events:queue  -> Orchestrator.handle_event  (rules -> queue tasks / notify / halt)
  tasks:queue   -> Orchestrator.run_task       (agent runs via the contract)

Run many of these for scale; work is idempotent-ish via task dedup. This is the
process that replaces the founder watching WhatsApp and assigning work by hand.
"""
import json
import time
import redis.exceptions
from .persistence import Persistence, EVENTS_Q, TASKS_Q
from .orchestrator.core import Orchestrator
from .scheduler import Scheduler


class Worker:
    def __init__(self, tick_interval=15):
        self.p = Persistence()
        self.orch = Orchestrator(load_config=self.p.load_config,
                                 load_context=self.p.load_context,
                                 persist=self.p)
        self.scheduler = Scheduler(self.p)
        self.tick_interval = tick_interval
        self._last_tick = 0.0

    def process_once(self, timeout=5) -> bool:
        """Handle one queue item. Returns True if something was processed."""
        # events (incl. control events like human.took_over) get priority over
        # pending work, so a human takeover is applied before queued Hermes tasks run
        # An idle blocking pop is NOT an error. Depending on the redis-py version,
        # an empty multi-key BLPOP either returns None or raises TimeoutError —
        # and if that exception escapes, run_forever() skips maybe_tick(), so on a
        # quiet system the SCHEDULER NEVER TICKS: trial expiries and lead
        # follow-ups would silently never fire. Treat both as "nothing waiting".
        try:
            item = self.p.r.blpop([EVENTS_Q, TASKS_Q], timeout=timeout)
        except redis.exceptions.TimeoutError:
            return False
        if item is None:
            return False
        queue, raw = item[0].decode(), item[1].decode()
        msg = json.loads(raw)
        tenant_id = msg["tenant_id"]

        if queue == EVENTS_Q:
            self.orch.handle_event(tenant_id, {"type": msg["type"],
                                               "payload": msg.get("payload", {})})
        else:  # TASKS_Q
            task_row = self.p.load_task(tenant_id, msg["task_id"])
            if task_row:                       # None => another worker already claimed it
                self.orch.run_task(tenant_id, task_row)
        return True

    def maybe_tick(self):
        """Run the scheduler if enough time has elapsed (time-based orchestration)."""
        now = time.time()
        if now - self._last_tick >= self.tick_interval:
            self._last_tick = now
            try:
                self.scheduler.tick()
            except Exception as e:
                print(f"[worker] scheduler error: {e}", flush=True)

    def run_forever(self):
        print("[worker] up — events + tasks + scheduler", flush=True)
        while True:
            try:
                self.process_once(timeout=5)
            except Exception as e:
                print(f"[worker] error: {e}", flush=True)
                time.sleep(1)
            # the time-based half must run even when queue handling just failed,
            # otherwise one bad task can stall every scheduled follow-up
            self.maybe_tick()


if __name__ == "__main__":
    Worker().run_forever()
