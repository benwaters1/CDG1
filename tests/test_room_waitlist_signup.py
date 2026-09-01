"""The dates a guest gave when nothing was free.

The booking page's no-availability state has been posting `source=waitlist`
with `wanted_arrival` and `wanted_departure` since the design side built it.
Nothing read either field, so somebody who said "write to me when that week
opens" was put on the newsletter and on nothing else — while the house already
had every part of the machinery to reach them: waitlist_entries is the table
notify_room_waitlist_opening looks in when a booking is cancelled, and there
is a page and a nightly job over the top of it.

So the wiring is small and the checks are mostly refusals. The one that
matters is the date. The restaurant waitlist had this fault exactly: an
unparseable date stored as typed left the guest on the list, thanked on the
way out, and permanently invisible to the only job that exists to find them.
A date column holding "sometime in June" is worse than an empty one, because
an empty one reads as missing.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-wls-"


def _iso(days):
    return (datetime.now(m.LOCAL_TZ).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM waitlist_entries WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM newsletter_subscribers WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action = 'newsletter_subscribe'")
    conn.commit()


def _entries(conn, email):
    return conn.execute(
        "SELECT * FROM waitlist_entries WHERE email = ? ORDER BY id", (email,)).fetchall()


def run():
    s = Suite("room waitlist signup")
    clients()
    conn = db()
    _cleanup(conn)
    anon = m.app.test_client()

    def post(email, **extra):
        conn.execute("DELETE FROM submission_log WHERE action = 'newsletter_subscribe'")
        conn.commit()
        data = {"email": email, "source": "waitlist",
                "wanted_arrival": _iso(40), "wanted_departure": _iso(44)}
        data.update(extra)
        return anon.post("/newsletter/subscribe", data=data, follow_redirects=True)

    s.section("The dates become a waiting list entry")
    who = TAG + "hopeful@example.invalid"
    post(who, name="A Hopeful", party_size="2")
    rows = _entries(conn, who)
    s.check("an entry is created", len(rows) == 1, detail=str(len(rows)))
    s.check("with the dates they asked for",
            rows and rows[0]["desired_arrival"] == _iso(40)
            and rows[0]["desired_departure"] == _iso(44),
            detail=None if not rows else f"{rows[0]['desired_arrival']}..{rows[0]['desired_departure']}")
    s.check("open, so the job that reaches people can see it",
            rows and rows[0]["status"] == "open",
            detail=None if not rows else rows[0]["status"])
    s.check("and it keeps the name and the party size",
            rows and rows[0]["name"] == "A Hopeful" and rows[0]["party_size"] == 2,
            detail=None if not rows else f"{rows[0]['name']}/{rows[0]['party_size']}")
    # It is still a newsletter signup. The form is one box and the guest asked
    # for both, so taking one and dropping the other would be a surprise.
    sub = conn.execute(
        "SELECT 1 FROM newsletter_subscribers WHERE email = ?", (who,)).fetchone()
    s.check("the newsletter signup still happens too", sub is not None)

    s.section("The form as it is actually posted, with no name")
    # The booking page's waiting-list form asks for an email and the dates and
    # nothing else. name is NOT NULL, so this is every real submission.
    bare = TAG + "noname@example.invalid"
    r = post(bare)
    s.check("a signup with no name is accepted", r.status_code == 200,
            detail="HTTP %s" % r.status_code)
    bare_rows = _entries(conn, bare)
    s.check("and lands on the list", len(bare_rows) == 1, detail=str(len(bare_rows)))
    s.check("named by the address, since that is what the house has",
            bare_rows and bare_rows[0]["name"] == bare,
            detail=None if not bare_rows else bare_rows[0]["name"])

    s.section("A date nobody can parse is refused, not stored as typed")
    vague = TAG + "vague@example.invalid"
    r = post(vague, wanted_arrival="sometime in June", wanted_departure="")
    s.check("the page does not fall over on prose", r.status_code == 200,
            detail="HTTP %s — a 500 also leaves no entry" % r.status_code)
    s.check("no entry is created from prose", _entries(conn, vague) == [],
            detail="a date column holding words is worse than an empty one")
    # The restaurant waitlist's fault, on the room side: on the list, thanked,
    # and invisible to the job.
    s.check("and nothing was written with an empty date",
            not any(r["desired_arrival"] in (None, "", "sometime in June")
                    for r in _entries(conn, vague)))

    s.section("Backwards is refused")
    backwards = TAG + "backwards@example.invalid"
    r = post(backwards, wanted_arrival=_iso(44), wanted_departure=_iso(40))
    s.check("the page does not fall over on a backwards range", r.status_code == 200,
            detail="HTTP %s — a 500 also leaves no entry" % r.status_code)
    s.check("a departure before the arrival makes no entry",
            _entries(conn, backwards) == [], detail=str(len(_entries(conn, backwards))))

    s.section("A plain newsletter signup is left alone")
    plain = TAG + "plain@example.invalid"
    conn.execute("DELETE FROM submission_log WHERE action = 'newsletter_subscribe'")
    conn.commit()
    anon.post("/newsletter/subscribe", data={"email": plain}, follow_redirects=True)
    s.check("no waiting list entry from the footer box", _entries(conn, plain) == [],
            detail="only the booking page's no-availability form is a waiting list")
    s.check("but they are on the newsletter", conn.execute(
        "SELECT 1 FROM newsletter_subscribers WHERE email = ?", (plain,)).fetchone() is not None)
    # Dates alone are not a request to be waitlisted. Without this the source
    # check is untestable, because the footer box sends no dates and the date
    # guard refuses it first whatever the source says.
    sneaky = TAG + "dates-no-source@example.invalid"
    conn.execute("DELETE FROM submission_log WHERE action = 'newsletter_subscribe'")
    conn.commit()
    anon.post("/newsletter/subscribe",
              data={"email": sneaky, "wanted_arrival": _iso(40),
                    "wanted_departure": _iso(44)}, follow_redirects=True)
    s.check("dates posted without asking for the waiting list make no entry",
            _entries(conn, sneaky) == [], detail=str(len(_entries(conn, sneaky))))

    s.section("Asking twice does not queue twice")
    # The guest who reloads, or tries again next week for the same week.
    post(who, name="A Hopeful", party_size="2")
    s.check("the same dates from the same address stay one entry",
            len(_entries(conn, who)) == 1, detail=str(len(_entries(conn, who))))
    post(who, name="A Hopeful", wanted_arrival=_iso(70), wanted_departure=_iso(74))
    s.check("but different dates are a second, real request",
            len(_entries(conn, who)) == 2, detail=str(len(_entries(conn, who))))

    s.section("What the house does with it already works")
    # The point of storing it. This is the job that runs when a booking is
    # cancelled, and it looks in exactly this table.
    # Asked of the real thing: this is the function a cancellation calls, and
    # the whole reason for storing the dates is that it can find them.
    with m.app.test_request_context():
        matched = m.matching_waitlist_entries(conn, _iso(40), _iso(44))
    s.check("the cancellation notifier finds the entry",
            any(r["email"] == who for r in matched),
            detail="%d entr(y/ies) matched those dates" % len(matched))
    # And a week nobody asked for must find nobody, or the check above would
    # pass just as well on a function that returned the whole table.
    with m.app.test_request_context():
        far = m.matching_waitlist_entries(conn, _iso(300), _iso(302))
    s.check("dates nobody asked for match nobody",
            not any((r["email"] or "").startswith(TAG) for r in far),
            detail="%d matched a week nobody wanted" % len(far))

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
