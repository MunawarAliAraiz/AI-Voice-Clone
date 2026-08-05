"""
AI Voice Clone Studio — Signed media URLs.

`<audio>` cannot send an Authorization header, so a media URL carries its own
proof in the query: `?t=<hmac>.<exp>`. The HMAC is over the resource path plus
the expiry, so a token for `history/42` cannot be replayed against `history/43`,
and it stops working at `exp`. This is why the client never constructs a media
URL itself — the server hands it a signed one in the generation response.

Deliberately NOT blob-fetch: an `<audio src>` with a signed query lets the
browser do ranged requests (seeking), which a `fetch()->blob` cannot.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from ..exceptions import MediaTokenError

__all__ = ["sign_token", "make_media_url", "verify_token"]


def _mac(resource: str, exp: int, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), f"{resource}:{exp}".encode(), hashlib.sha256
    ).hexdigest()


def sign_token(resource: str, secret: str, ttl_sec: int, *, now: int | None = None) -> str:
    """Return `<hmac>.<exp>` for `resource`, valid for `ttl_sec` seconds."""
    exp = int(time.time() if now is None else now) + ttl_sec
    return f"{_mac(resource, exp, secret)}.{exp}"


def make_media_url(resource: str, secret: str, ttl_sec: int) -> str:
    """Full relative URL the client can drop straight into `<audio src>`."""
    return f"/api/media/{resource}?t={sign_token(resource, secret, ttl_sec)}"


def verify_token(resource: str, token: str, secret: str, *, now: int | None = None) -> None:
    """
    Raise `MediaTokenError` unless `token` is a valid, unexpired signature for
    `resource`. Uses a constant-time compare — a timing side channel on a media
    URL is a small leak, but it is free to close.
    """
    try:
        mac, exp_str = token.rsplit(".", 1)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        raise MediaTokenError("Malformed media token.") from None
    if exp < int(time.time() if now is None else now):
        raise MediaTokenError("Media token has expired.")
    if not hmac.compare_digest(mac, _mac(resource, exp, secret)):
        raise MediaTokenError("Invalid media token signature.")
