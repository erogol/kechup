"""Agent identity resolution (spec section 2)."""

from __future__ import annotations

import os

from kechup.errors import KechupError


def resolve_agent(by_flag: str | None) -> str:
    """`--by` wins when both are set. Both values are stripped first (R4):
    a `--by` given but blank after stripping refuses; a blank
    `KECHUP_AGENT` is treated as missing, not as a valid fallback. Refuses
    when both are missing or blank: exit 3."""
    if by_flag is not None:
        stripped_by = by_flag.strip()
        if not stripped_by:
            raise KechupError(3, "no_agent_id", "no agent id: set --by or KECHUP_AGENT")
        return stripped_by
    env_value = os.environ.get("KECHUP_AGENT")
    if env_value is not None:
        stripped_env = env_value.strip()
        if stripped_env:
            return stripped_env
    raise KechupError(3, "no_agent_id", "no agent id: set --by or KECHUP_AGENT")
