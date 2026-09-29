"""Application error type used across gateway, engine and rules."""

from __future__ import annotations


class GameError(Exception):
    """Raised for expected, client-visible failures.

    ``code`` is a stable machine readable string the frontend can switch on,
    ``message`` is a short human readable sentence.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}

    def __repr__(self) -> str:
        return f"GameError(code={self.code!r}, message={self.message!r})"
