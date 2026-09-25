"""ARCH46-S1:api-public-feeds — the subscribable iCal feed of a workspace's obligations.

No session: the credential is the signed token in the path (registered in
app/core/public_route_registry.PUBLIC_ROUTES with the public rate-limit
policy). The token's signature is checked in constant time before the
database is asked; only its SHA-256 is stored. A malformed, unsigned,
unknown, revoked or expired token, a member who lost access and a plan that
lapsed all get the IDENTICAL 404, and nothing is cached (Cache-Control:
no-store), so a revocation takes effect on the calendar app's next poll.
The token is never logged.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.obligations import service
from app.services.obligations import temporal as T
from app.services.obligations import vocabulary as v

router = APIRouter(tags=["Calendar feeds (public)"])

_HEADERS = {"Cache-Control": "no-store, private, max-age=0", "Pragma": "no-cache", "X-Robots-Tag": "noindex, nofollow",
            "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}


def _not_found() -> Response:
    return Response(content="Not found.\n", status_code=404, media_type="text/plain; charset=utf-8", headers=_HEADERS)


@router.get("/public/calendar-feeds/{token}.ics")
def calendar_feed(token: str, db: Session = Depends(get_db)) -> Response:
    feed = service.resolve_feed(db, token)
    if feed is None:
        return _not_found()
    ws = service.workspace_row(db, feed.workspace_id)
    at = service.now()
    today = T.local_today(at, ws.timezone)
    _, known = T.zone(ws.timezone)
    obs = service.feed_obligations(db, workspace_id=feed.workspace_id,
                                   owner=feed.user_id if feed.scope == v.FEED_SCOPE_MINE else None,
                                   include_closed=feed.include_closed, today=today)
    body = service.render_ical(db, obligations=obs, name=f"{ws.workspace_name} — {feed.label}",
                               zone=ws.timezone if known else "UTC", at=at, today=today,
                               prefix=service.link_prefix(db, feed.workspace_id))
    service.touch_feed(db, feed)
    db.commit()
    return Response(content=body.encode("utf-8"), media_type="text/calendar; charset=utf-8",
                    headers={**_HEADERS, "Content-Disposition": 'inline; filename="obligations.ics"'})


__all__ = ["router"]
