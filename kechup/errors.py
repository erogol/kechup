"""Error type used across kechup. Carries an exit code and a machine code."""

from __future__ import annotations

from typing import Any


class KechupError(Exception):
    """An error with an exit code (section 5) and a machine error code.
    `data` is optional structured detail for the `--json` error envelope
    (FIX_SPEC F1, e.g. the list of unsafe files)."""

    def __init__(
        self, exit_code: int, code: str, message: str, data: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.code = code
        self.message = message
        self.data = data
