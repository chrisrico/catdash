"""Web Push to the browsers that have opened the dashboard.

Every browser that opens the Live tab subscribes on its first click
(web/src/lib/push.js, catdash/sw.js); the server keeps the subscription
(db.push_subscriptions) and sends through the browser vendor's push service
when the stuck-robot watchdog has something to say. No third party of our own
in the middle and nothing to install: the one requirement is HTTPS, which the
Tailscale service provides (plain http://localhost also works for development).

Every send is signed with a VAPID key pair (RFC 8292), generated the first time
it is needed and kept as a PEM beside the database — a new key would orphan
every subscription, so it lives in the data volume, not the image. The `sub`
claim is the dashboard's own address when DASHBOARD_URL is https (Apple's push
service wants a real contact), a placeholder mailto otherwise.

`send_to_all` never raises: a notification is a courtesy on top of work already
done. A subscription the push service says is gone (404/410) is deleted; any
other failure is noted on the row and the next send tries it again.
"""

from __future__ import annotations

import json
import logging
import threading
from base64 import urlsafe_b64encode
from pathlib import Path

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from . import db
from .config import Settings, get_settings

logger = logging.getLogger("push")

KEY_FILENAME = "vapid_private.pem"
FALLBACK_SUBJECT = "mailto:catdash@localhost"
# A push the service can't deliver at once (phone asleep) is held this long.
# A "check the robot" is stale after a few hours, not days.
TTL_S = 6 * 3600
SEND_TIMEOUT_S = 10
GONE_STATUSES = (404, 410)

_lock = threading.Lock()
_cache: dict[Path, Vapid] = {}


def key_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return Path(settings.db_path).parent / KEY_FILENAME


def vapid(settings: Settings | None = None) -> Vapid:
    """The server's key pair, generated and saved on first use."""
    path = key_path(settings)
    with _lock:
        if path not in _cache:
            if path.exists():
                _cache[path] = Vapid.from_file(str(path))
            else:
                key = Vapid()
                key.generate_keys()
                path.parent.mkdir(parents=True, exist_ok=True)
                key.save_key(str(path))
                path.chmod(0o600)
                logger.info("generated a VAPID key pair at %s", path)
                _cache[path] = key
        return _cache[path]


def public_key(settings: Settings | None = None) -> str:
    """The applicationServerKey the browser subscribes with: the raw
    uncompressed P-256 point, base64url without padding."""
    raw = vapid(settings).public_key.public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    return urlsafe_b64encode(raw).decode().rstrip("=")


def subject(settings: Settings | None = None) -> str:
    url = (settings or get_settings()).dashboard_url
    return url if url.startswith("https://") else FALLBACK_SUBJECT


def tap_url(settings: Settings | None = None) -> str | None:
    """Where a tapped notification opens, or None (sw.js then opens the origin
    the notification came from)."""
    url = (settings or get_settings()).dashboard_url
    return url.rstrip("/") + "/" if url else None


def payload(*, title: str, body: str, url: str | None, tag: str) -> str:
    """What sw.js unpacks: the text, where a tap goes, and a tag so a repeat
    about the same robot replaces the earlier card instead of stacking."""
    return json.dumps({"title": title, "body": body, "url": url, "tag": tag})


def send_one(row: dict, data: str, settings: Settings | None = None) -> str | None:
    """None when the push service accepted it; otherwise a short reason.
    A subscription the service says is gone is reported as "gone"."""
    settings = settings or get_settings()
    try:
        webpush(
            {"endpoint": row["endpoint"], "keys": {"p256dh": row["p256dh"], "auth": row["auth"]}},
            data=data,
            vapid_private_key=vapid(settings),
            vapid_claims={"sub": subject(settings)},
            ttl=TTL_S,
            timeout=SEND_TIMEOUT_S,
        )
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in GONE_STATUSES:
            return "gone"
        reason = str(getattr(exc, "message", exc))[:160]
        return f"HTTP {status}: {reason}" if status else reason
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        return f"{type(exc).__name__}: {str(exc)[:160]}"
    return None


def send_to_all(*, title: str, body: str, tag: str = "catdash") -> dict:
    """One push per subscribed browser. Returns {"sent", "failed", "gone"}
    counts; the log has the rest. Blocking (pywebpush uses requests) — call
    from a thread when on the event loop."""
    counts = {"sent": 0, "failed": 0, "gone": 0}
    settings = get_settings()
    data = payload(title=title, body=body, url=tap_url(settings), tag=tag)
    for row in db.list_push_subscriptions():
        error = send_one(row, data, settings)
        if error == "gone":
            db.delete_push_subscription(row["endpoint"])
            counts["gone"] += 1
            logger.info("push subscription retired (gone): %s", row["endpoint"][:60])
        elif error:
            db.mark_push_result(row["endpoint"], error)
            counts["failed"] += 1
            logger.warning("push failed for %s: %s", row["endpoint"][:60], error)
        else:
            db.mark_push_result(row["endpoint"], None)
            counts["sent"] += 1
    if counts["sent"]:
        logger.info("pushed to %d browser(s): %s", counts["sent"], title)
    elif not db.count_push_subscriptions():
        logger.warning("no browser is subscribed to push; nothing sent: %s", title)
    return counts
