"""The owner can clear a line from "Nobody has told you this" -- until it changes.

The owner asked for it (24 November 2026: "i cant clear notifications"), and
the house rule it has to live with is that the panel must be able to be empty
and must never become a record of every problem the house has had. So a
clearing is keyed on the line as it stood -- severity, title, count -- and the
line comes back by itself the moment any of those moves. Clearing hides; it
never fixes, deletes, or settles anything anywhere else.

The checks that matter are the ones that bring a line back: a dismissal that
outlives a change is the failure this exists to prevent, and it renders
perfectly while doing it.
"""
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m


def _line(title="Ztest police register has gaps", count=8, severity="warn",
          detail="8 guests who arrived with no line on it."):
    return {"severity": severity, "title": title, "detail": detail,
            "count": count, "href": "/admin/police-register"}


def run():
    s = Suite("home warning clear")
    oc, ec, _owner, _emp = clients()
    conn = db()
    real = m.owner_home_warnings
    live = {"lines": [_line()]}
    # The panel is made to say exactly what each check needs. Everything that
    # reads it -- the home page, the clear route -- reaches it by this global,
    # so this is the same seam the real list goes through.
    m.owner_home_warnings = lambda _c, _t: [dict(w) for w in live["lines"]]
    conn.execute("DELETE FROM home_warning_dismissals")
    conn.commit()
    try:
        return _run(s, oc, ec, conn, live)
    finally:
        m.owner_home_warnings = real
        conn.execute("DELETE FROM home_warning_dismissals")
        conn.commit()
        conn.close()


def _page(oc, q=""):
    return oc.get("/" + q).get_data(as_text=True)


def _rows(conn):
    return conn.execute("SELECT * FROM home_warning_dismissals").fetchall()


def _run(s, oc, ec, conn, live):
    today = m.house_today()
    fp = m.home_warning_fingerprint

    s.section("Which line is which")
    base = _line()
    s.check("the detail is not part of it (its ages move every morning)",
            fp(base) == fp(dict(base, detail="Robert wrote 37 days ago.")))
    s.check("the count is", fp(base) != fp(dict(base, count=9)))
    s.check("so is the severity -- a warning turning blocker is a new line",
            fp(base) != fp(dict(base, severity="blocker")))
    s.check("and the title", fp(base) != fp(dict(base, title="Ztest other")))

    s.section("Each line can be cleared from the home page")
    page = _page(oc)
    key = fp(base)
    s.check("the line is on the panel", "Ztest police register has gaps" in page)
    s.check("with a Clear button carrying its key",
            f'value="{key}"' in page and ">Clear</button>" in page)
    s.check("and a Snooze", "Snooze 7 days</button>" in page)
    s.check("the form posts with a CSRF token",
            'action="/home/warnings/clear"' in page and 'name="csrf_token"' in page)

    r = oc.post("/home/warnings/clear", data={"key": key, "how": "dismiss"})
    s.check("clearing answers with a way back to the panel",
            r.status_code == 302 and "nobody-told" in r.headers.get("Location", ""), r)
    rows = _rows(conn)
    s.check("and is written down, until it changes",
            len(rows) == 1 and rows[0]["until_date"] is None
            and rows[0]["title"] == base["title"], detail=str([dict(x) for x in rows]))
    page = _page(oc)
    # The panel, not the page: the flash confirming the clearing names it.
    panel = page.split('id="nobody-told"', 1)[-1].split("cleared — show", 1)[0]
    s.check("the line is gone from the panel",
            "Ztest police register has gaps" not in panel
            and f'value="{key}"' not in page, detail=panel[:300])
    s.check("but '1 cleared -- show' is there, so nothing is lost for good",
            "1 cleared — show" in page)
    s.check("and the greeting no longer says something needs looking at",
            "need looking at" not in page and "needs looking at" not in page)
    s.check("the panel calms down rather than framing 'nothing new' in red",
            "oh-warn--quiet" in page and "Nothing new." in page)
    shown = _page(oc, "?cleared=1")
    s.check("asking to see them lists it, with Bring back",
            "Ztest police register has gaps" in shown and "Bring back</button>" in shown)
    s.check("the clearing was audited",
            conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = "
                         "'home_warning_cleared' AND target = ?",
                         (base["title"],)).fetchone()[0] >= 1)

    s.section("It comes back the moment it changes")
    live["lines"] = [_line(count=9, detail="9 guests who arrived with no line on it.")]
    page = _page(oc)
    s.check("8 gaps cleared, 9 gaps shown", "Ztest police register has gaps" in page
            and "cleared — show" not in page)
    s.check("and the spent clearing is gone, so a different 8 later is not hidden",
            _rows(conn) == [], detail=str([dict(x) for x in _rows(conn)]))
    live["lines"] = [_line()]
    s.check("back to 8: shown, not quietly hidden again",
            "Ztest police register has gaps" in _page(oc))
    oc.post("/home/warnings/clear", data={"key": key, "how": "dismiss"})
    live["lines"] = [_line(severity="blocker")]
    s.check("a cleared warning that becomes a blocker is back",
            "Ztest police register has gaps" in _page(oc))
    live["lines"] = [_line()]
    s.check("a line that goes away entirely takes its clearing with it",
            _rows(conn) == [])

    s.section("A snooze lapses on its own")
    oc.post("/home/warnings/clear", data={"key": key, "how": "snooze"})
    rows = _rows(conn)
    want = (today + timedelta(days=m.HOME_WARNING_SNOOZE_DAYS)).isoformat()
    s.check("a snooze is written with its end date",
            len(rows) == 1 and rows[0]["until_date"] == want,
            detail=str([dict(x) for x in rows]))
    shown_now, hidden_now = m.split_cleared_warnings(conn, [_line()], today)
    s.check("hidden for the week", not shown_now and len(hidden_now) == 1)
    s.check("the panel says when it comes back",
            "snoozed until" in _page(oc, "?cleared=1"))
    later = today + timedelta(days=m.HOME_WARNING_SNOOZE_DAYS)
    shown_later, hidden_later = m.split_cleared_warnings(conn, [_line()], later)
    s.check("and shown again on the day it ends, unchanged or not",
            len(shown_later) == 1 and not hidden_later)

    s.section("Bring back")
    other = _line(title="Ztest held messages too old to send", count=129)
    live["lines"] = [_line(), other]
    oc.post("/home/warnings/clear", data={"key": key, "how": "dismiss"})
    oc.post("/home/warnings/clear", data={"key": fp(other), "how": "dismiss"})
    s.check("two cleared", len(_rows(conn)) == 2)
    s.check("'Bring them all back' is offered once there are two",
            "Bring them all back" in _page(oc, "?cleared=1"))
    oc.post("/home/warnings/restore", data={"key": key})
    page = _page(oc)
    s.check("one brought back is on the panel again, the other still cleared",
            "Ztest police register has gaps" in page and "1 cleared — show" in page)
    r = oc.post("/home/warnings/restore", data={"key": "all"})
    s.check("all brought back", r.status_code == 302 and _rows(conn) == [], r)

    s.section("What it refuses")
    r = oc.post("/home/warnings/clear", data={"key": "0123456789abcdef", "how": "dismiss"})
    s.check("a line that has already changed is not cleared",
            r.status_code == 302 and _rows(conn) == [], r)
    r = ec.post("/home/warnings/clear", data={"key": key, "how": "dismiss"})
    s.check("a member of staff cannot clear the owner's panel",
            r.status_code in (302, 403) and _rows(conn) == [], r)
    r = ec.post("/home/warnings/restore", data={"key": "all"})
    s.check("nor bring lines back", r.status_code in (302, 403), r)

    s.section("Clearing hides it here and nowhere else")
    oc.post("/home/warnings/clear", data={"key": key, "how": "dismiss"})
    s.check("the full list still has it (digest, watch tasks, assistant)",
            any(w["title"] == base["title"] for w in m.owner_home_warnings(conn, today)))
    with m.app.test_request_context():
        said = m.assistant_read_tool(conn, {"id": 0, "role": "owner"}, "get_warnings", {})
    s.check("the assistant still says it when asked", base["title"] in said, detail=said[:200])
    return s


if __name__ == "__main__":
    print(run().report())
