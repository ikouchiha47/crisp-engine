"""Agent adapter layer.

Normalizes agent-specific hook payloads (Claude Code, OpenCode, Pi) into a
single NormalizedEvent that hooks.py and all handlers consume. Adding support
for a new agent means writing one new adapter class -- nothing else changes.

Usage:
    from lib.adapters import resolve
    event = resolve(sys.argv, payload)
"""

from lib.adapters.registry import resolve

__all__ = ["resolve"]
