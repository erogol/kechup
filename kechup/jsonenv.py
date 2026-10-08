"""The --json envelope (spec section 5)."""

from __future__ import annotations

import json
from typing import Any


def ok_envelope(command: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"kechup_json": 1, "ok": True, "command": command, "data": data}


def err_envelope(
    command: str, code: str, message: str, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "kechup_json": 1,
        "ok": False,
        "command": command,
        "error": {"code": code, "message": message},
    }
    if data is not None:
        envelope["data"] = data
    return envelope


def dump(envelope: dict[str, Any]) -> str:
    return json.dumps(envelope, indent=2, sort_keys=True)
