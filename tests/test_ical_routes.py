"""The three iCal ROUTES, which is the half test_ical_sync does not reach.

test_ical_sync already holds the machinery, and holds it well: the parser, the
wholesale replace, the fail-safe when a feed goes down, and whether a channel
block actually stops a booking going through. It drives all of that through
sync_ical_source directly.

What it does not do is press the buttons. It checks that an employee cannot
add a feed or force a sync -- the refusal -- and that is exactly the shape the
coverage measure turned up: new_ical_source, sync_ical_source_now and
api_sync_ical had only ever answered 403 or 404, so the owner's own path
through them had never run. The form's validation, the flash the owner reads,
and the scheduler endpoint's token posture were all unexercised.

So this is the route half, and it deliberately does not re-prove the sync
behaviour that test_ical_sync owns.

No network: app.py imports urlopen by name, so a stand-in in its place answers
from memory. Checked at the end, because leaving it replaced would make every
later suite believe the network was unreachable.
"""
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today

import _harness

m = _harness.m
TAG = "ZZICR"
TOKEN = "zz-ical-route-token-0123456789"


def _ics(ranges):
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN"]
    for n, (start, end) in enumerate(ranges):
        out += ["BEGIN:VEVENT", f"UID:{TAG}-{n}",
                "DTSTART;VALUE=DATE:" + start.strftime("%Y%m%d"),
                "DTEND;VALUE=DATE:" + end.strftime("%Y%m%d"),
                "SUMMARY:CLOSED - Not available", "END:VEVENT"]
    out.append("END:VCALENDAR")
    return ("\r\n".join(out)).encode("utf-8")


class _Feed:
    """urlopen, answering from memory or refusing, and never a socket.

    Replaces the name app.py bound, not urllib's own: app.py does
    `from urllib.request import urlopen`, so patching the module would leave
    the bound name pointing at the real function and this suite would quietly
    go to the network.
    """

    def __init__(self, body=None, error=None):
        self.body, self.error, self.calls = body, error, []

    def __enter__(self):
        self.real = m.urlopen

        class _Resp:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        def _fake(req, timeout=None):
            self.calls.append(getattr(req, "full_url", str(req)))
            if self.error:
                raise self.error
            return _Resp(self.body)

        m.urlopen = _fake
        return self

    def __exit__(self, *_exc):
        m.urlopen = self.real
        return False


def _cleanup(conn):
    conn.execute(
        "DELETE FROM blocked_dates WHERE ical_source_id IN "
        "(SELECT id FROM ical_sources WHERE label LIKE ?)", (TAG + "%",))
    conn.execute(
        "DELETE FROM ical_sync_log WHERE ical_source_id IN "
        "(SELECT id FROM ical_sources WHERE label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM ical_sources WHERE label LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("the iCal routes an owner actually presses")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    today = house_today()

    room = conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    if not room:
        s.section("Setup")
        s.check("a room exists", False, detail="reported rather than skipped")
        conn.close()
        return s

    a1, a2 = today + timedelta(days=400), today + timedelta(days=403)

    s.section("Adding a feed needs both halves")
    r = oc.post(f"/admin/rooms/{room['id']}/ical-sources/new",
                data={"label": TAG + " no url", "url": ""},
                follow_redirects=True)
    s.check("a label with no URL is refused",
            "Both a label and a URL are required" in " ".join(flashes(r)),
            detail=str(flashes(r))[:120])
    s.check("and nothing is created",
            conn.execute("SELECT COUNT(*) FROM ical_sources WHERE label = ?",
                         (TAG + " no url",)).fetchone()[0] == 0)

    s.section("With both, it is added and synced on the spot")
    # The first sync happens as part of adding it, so the owner finds out the
    # URL is wrong now rather than at the next scheduled run.
    with _Feed(body=_ics([(a1, a2)])) as feed:
        r = oc.post(f"/admin/rooms/{room['id']}/ical-sources/new",
                    data={"label": TAG + " feed",
                          "url": "https://example.invalid/" + TAG + ".ics"},
                    follow_redirects=True)
    source = conn.execute("SELECT * FROM ical_sources WHERE label = ?",
                          (TAG + " feed",)).fetchone()
    s.check("the feed is on the room", source is not None)
    s.check("and it was fetched straight away",
            bool(feed.calls) and TAG in feed.calls[0], detail=str(feed.calls))
    if not source:
        _cleanup(conn)
        conn.close()
        return s
    s.check("the dates it carried are blocked",
            conn.execute(
                "SELECT COUNT(*) FROM blocked_dates WHERE ical_source_id = ?",
                (source["id"],)).fetchone()[0] == 1)

    s.section("A URL that does not answer is said so at the time")
    with _Feed(error=OSError("connection reset by peer")):
        r = oc.post(f"/admin/ical-sources/{source['id']}/sync",
                    follow_redirects=True)
    said = " ".join(flashes(r))
    s.check("the owner is told the sync failed", "Sync failed" in said,
            detail=said or "nothing was said")
    # Lowered on both sides: searching a lowered haystack for "check the URL"
    # can never match, and the check would have read as a missing message.
    s.check("and told where to look", "check the url" in said.lower(),
            detail="a failure with no next step is a page somebody retries")

    s.section("And a good one says so too")
    with _Feed(body=_ics([(a1, a2)])):
        r = oc.post(f"/admin/ical-sources/{source['id']}/sync",
                    follow_redirects=True)
    s.check("it says synced", "Synced" in " ".join(flashes(r)),
            detail=str(flashes(r))[:120])

    s.section("The scheduler's endpoint, and what it tells a prober")
    was = getattr(m, "ICAL_SYNC_TOKEN", "")
    m.ICAL_SYNC_TOKEN = TOKEN
    try:
        c = m.app.test_client()
        s.check("a wrong token is a 404, not a 403",
                c.get("/api/sync-ical?token=wrong").status_code == 404,
                detail="403 tells a prober there is something here")
        with _Feed(body=_ics([(a1, a2)])) as feed:
            r = c.get(f"/api/sync-ical?token={TOKEN}")
        s.check("the right one runs it", r.status_code == 200,
                detail=f"status {r.status_code}")
        s.check("and it actually went and looked", bool(feed.calls),
                detail=str(feed.calls[:2]))
    finally:
        m.ICAL_SYNC_TOKEN = was
    s.check("and the token is off again",
            m.app.test_client().get(
                f"/api/sync-ical?token={TOKEN}").status_code == 404,
            detail="left set, every later suite would be running an app with "
                   "a live scheduler credential")

    s.section("No socket was opened by any of it")
    s.check("urlopen is app.py's own again",
            getattr(m.urlopen, "__name__", "") != "_fake",
            detail="left replaced, every later suite would believe the "
                   "network was unreachable")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
