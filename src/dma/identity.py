"""Who is calling. Tools and the gate ask a resolver, so they never depend on the transport.

stdio: one local user, named by DMA_USER or the OS user. It is an audit label only, with no id, as in v1.
HTTP: the verified access token of the current request gives a stable id (the Entra oid) and a label. The
resolver for that mode lives with the auth code and never falls back to DMA_USER or to a tool argument.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Caller:
    id: str | None
    label: str


CallerResolver = Callable[[], Caller]


class CallerUnknown(RuntimeError):
    """No verified caller for this request. Raised instead of guessing an identity."""


def static_caller(label: str) -> CallerResolver:
    caller = Caller(id=None, label=label)
    return lambda: caller
