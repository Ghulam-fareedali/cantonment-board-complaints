"""The Agent Contract.

Every one of the 10 agents implements THIS and nothing else talks to it.
Agents never call each other directly — only the orchestrator does, by handing
them a Task plus read-only context. Agents return a structured AgentResult; the
orchestrator applies the side effects (events, context writes, human gates).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    id: str
    tenant_id: str
    agent: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class Context:
    """Read-only view an agent gets: the tenant config + shared context_store."""
    tenant_id: str
    config: dict[str, Any]
    store: dict[str, Any]


@dataclass
class AgentResult:
    status: str                                   # 'done' | 'needs_human' | 'failed'
    result: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)          # to emit
    context_writes: dict[str, Any] = field(default_factory=dict)        # shared memory
    human_review: bool = False                    # gate before irreversible side effects


class Agent:
    """Base class. Subclass and set `name`, implement `handles` + `run`."""
    name: str = "base"

    def handles(self, task_type: str) -> bool:
        raise NotImplementedError

    def run(self, task: Task, ctx: Context) -> AgentResult:
        raise NotImplementedError

    # convenience for subclasses: read a business fact from tenant config, never hardcode
    @staticmethod
    def price_of(ctx: Context, service: str) -> float | None:
        for s in ctx.config.get("services", []):
            if s.get("name", "").lower() == service.lower():
                return s.get("price")
        return None
