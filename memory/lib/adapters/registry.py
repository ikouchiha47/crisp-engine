"""Adapter registry -- resolves argv + payload to a NormalizedEvent."""

from lib.adapters.base import NormalizedEvent
from lib.adapters.claude_code import ClaudeCodeAdapter
from lib.adapters.opencode import OpenCodeAdapter
from lib.adapters.pi import PiAdapter
from lib.adapters.internal import InternalAdapter

# Order matters: more specific adapters first, internal last
_ADAPTERS = [
    ClaudeCodeAdapter(),
    OpenCodeAdapter(),
    PiAdapter(),
    InternalAdapter(),
]


def resolve(argv: list[str], payload: dict) -> NormalizedEvent:
    """Return a NormalizedEvent for this invocation.

    Tries each adapter in order; the first whose can_handle() returns True
    owns the normalization. Raises ValueError if nothing matches.
    """
    for adapter in _ADAPTERS:
        if adapter.can_handle(argv, payload):
            return adapter.normalize(argv, payload)
    raise ValueError(
        f"No adapter matched argv={argv!r} hook_event={payload.get('hook_event_name')!r}"
    )
