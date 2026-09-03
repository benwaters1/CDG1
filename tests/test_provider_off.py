"""The buttons this house presses today, with nothing behind them.

The château has no accounting connection, no SMS provider and no model
provider configured. So for four owner-facing buttons, the branch that runs
EVERY time somebody presses them today is the one that cannot do the job --
and that is precisely the branch nothing tested. The suite reached all four,
got a 403 or a 404 from the door, and counted them.

A button that cannot do its job has two duties, and they are easy to get
wrong in opposite directions:

  IT MUST SAY SO. Not a green banner, not silence, not a page that reloads
  looking the same. "Couldn't sync: Pennylane isn't connected." is a sentence
  somebody can act on.

  AND IT MUST NOT HALF-DO IT. No audit line for a sync that did not happen --
  a log saying the accounts were pulled, on a day they were not, is worse
  than no log. Nothing marked as sent that was not sent.

The text job is the interesting one, because its answer is not simply "no".
Its own comment: a message is stamped whether it went now or is waiting,
"because the outbox holds it either way and stamping only on success would
send it twice the day a provider is switched on". So a guest with a usable
number is stamped even with no provider — and a guest whose number cannot be
texted at all is NOT, because nothing is holding anything for them. Both
directions are here.

NOTHING REACHES A THIRD PARTY, and the seam matters. The guard LIVES inside
_pennylane_request, so replacing that function removes the thing under test --
the first version of this did exactly that and the route sailed straight into
the stand-in. urlopen is replaced instead, underneath the guard, which leaves
the real check running and turns any escape from it into a loud failure rather
than a live call with the token from .env.
"""
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today

import _harness

m = _harness.m
TAG = "ZZOFF"


class _NoNetwork:
    """The network itself made to raise, underneath the guard.

    The first version of this replaced _pennylane_request -- which is where
    the "isn't connected" guard LIVES, so it removed the very thing under
    test and the route sailed past into a stand-in that raised. The seam has
    to be below the guard, not around it.

    urlopen is what _pennylane_request eventually calls, and app.py imports
    it by name. Replacing that leaves the real guard running and turns any
    escape from it into a loud failure rather than a live call to Paris with
    the token from .env.
    """

    def __enter__(self):
        self.real_urlopen = m.urlopen
        self.real_request = m._pennylane_request
        # The harness replaces _pennylane_request with a raiser, and the
        # guard lives inside it -- so with the raiser in place the route
        # 500s here where the owner would read a sentence. The real one is
        # kept by the harness for exactly this, and goes back for a few
        # lines with the network blocked underneath it.
        m._pennylane_request = _harness.REAL_PENNYLANE_REQUEST

        def _refuse(*_a, **_kw):
            raise AssertionError(
                "a request reached the network. The 'isn't connected' guard "
                "is supposed to answer before this.")

        m.urlopen = _refuse
        return self

    def __exit__(self, *_exc):
        m.urlopen = self.real_urlopen
        m._pennylane_request = self.real_request
        return False


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM audit_log WHERE target LIKE ?", ("%" + TAG + "%",))
    conn.commit()


def run():
    s = Suite("the buttons with nothing behind them")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()
    today = house_today()

    def rowid():
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def audits(kind):
        return conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = ?",
            (kind,)).fetchone()[0]

    s.section("Setup is the state the château is actually in")
    s.check("no accounting connection is configured",
            m.pennylane_configured() is False,
            detail="if this ever reads True here, the suite is one guard away "
                   "from a live ledger")

    # ------------------------------------------------------------ accounts
    s.section("Pulling the chart of accounts, with nothing connected")
    before = audits("pennylane_accounts_synced")
    with _NoNetwork():
        r = oc.post("/admin/pennylane/sync", follow_redirects=True)
    said = " ".join(flashes(r))
    # Matched around the apostrophes: the page escapes them to &#39;, so
    # searching for the sentence as written in app.py fails against a page
    # that is saying exactly the right thing.
    s.check("it says it could not", "sync: Pennylane" in said,
            detail=said or "nothing was said")
    s.check("and names the reason", "connected" in said,
            detail="a failure with no reason is a button somebody presses "
                   "again")
    s.check("nothing is written to the audit log",
            audits("pennylane_accounts_synced") == before,
            detail="a line saying the accounts were pulled, on a day they "
                   "were not, is worse than no line")

    # -------------------------------------------------------------- events
    s.section("Sending an event's revenue, same")
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(event_inquiries)")}
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token,
                   event_type, contact_name, contact_email, preferred_date,
                   guest_count, status, quoted_price, created_at)
           VALUES (?, ?, 'wedding', ?, ?, ?, 40, 'confirmed', 4500, ?)""",
        (TAG + "EV", (TAG + "ev").lower(), TAG + " Party",
         f"{TAG}.p@example.invalid".lower(),
         (today - timedelta(days=10)).isoformat(), now))
    event = rowid()
    conn.commit()
    before = audits("event_revenue_sent_to_pennylane")
    with _NoNetwork():
        r = oc.post(f"/management/revenue-to-send/event/{event}",
                    follow_redirects=True)
    said = " ".join(flashes(r))
    s.check("it answers rather than erroring", r.status_code == 200,
            detail=f"status {r.status_code}")
    s.check("and says something about why", bool(said.strip()),
            detail="a button that reloads the page unchanged is one nobody "
                   "can tell worked or not")
    s.check("nothing is logged as sent",
            audits("event_revenue_sent_to_pennylane") == before,
            detail="the audit line is written only when it went, and that is "
                   "the whole of what the accountant relies on")
    if "pennylane_invoice_id" in cols:
        s.check("and the event is not marked as invoiced",
                conn.execute(
                    "SELECT pennylane_invoice_id FROM event_inquiries "
                    "WHERE id = ?", (event,)).fetchone()[
                    "pennylane_invoice_id"] is None)

    # --------------------------------------------------------------- texts
    s.section("Tomorrow's arrivals, with no SMS provider")
    room = conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    arrival = (today + timedelta(days=1)).isoformat()

    def make_arrival(ref, phone):
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token,
                       guest_name, guest_email, guest_phone, arrival_date,
                       departure_date, party_size, status, total_price,
                       created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 300, ?)""",
            (room["id"], TAG + ref, (TAG + ref).lower(), TAG + " " + ref,
             f"{TAG}.{ref}@example.invalid".lower(), phone, arrival,
             (today + timedelta(days=3)).isoformat(), now))
        return rowid()

    mobile = make_arrival("MOB", "+33612345678")
    landline = make_arrival("LAN", "+33561000000")
    conn.commit()

    r = oc.post("/management/texting/run-checkin", data={"kind": "checkin"},
                follow_redirects=True)
    said = " ".join(flashes(r))
    s.check("the job reports in plain words", bool(said.strip()),
            detail=said or "nothing was said")
    # The app's own words, which are better than "held" and "skipped": it
    # says what is waiting and what it could not use.
    s.check("and says what it could not do",
            "waiting for a provider" in said.lower()
            and "no number we could use" in said.lower(),
            detail=said)

    def stamped(bid):
        return conn.execute(
            "SELECT checkin_text_sent_at FROM bookings WHERE id = ?",
            (bid,)).fetchone()["checkin_text_sent_at"]

    s.check("a guest with a usable number IS stamped",
            stamped(mobile) is not None,
            detail="the outbox holds it either way, and stamping only on "
                   "success would text them twice the day a provider is "
                   "switched on")
    s.check("and one whose number cannot be texted is NOT",
            stamped(landline) is None,
            detail="nothing is being held for them, so a stamp here is a "
                   "message that never arrives and is never chased")

    s.section("And a kind of message the app does not have is refused")
    r = oc.post("/management/texting/run-checkin", data={"kind": "birthday"},
                follow_redirects=False)
    s.check("it is a 400, not a KeyError", r.status_code == 400,
            detail=f"status {r.status_code}")

    # -------------------------------------------------------- the drafting
    s.section("The add-in's drafting, with no model provider")
    was = getattr(m, "GUEST_LOOKUP_TOKEN", "")
    s.check("without a token it is a 404 like its siblings",
            m.app.test_client().post(
                "/api/draft-reply", data={"token": "x"}).status_code == 404,
            detail="the same posture, so a prober learns nothing")
    m.GUEST_LOOKUP_TOKEN = "zz-draft-token-0123456789"
    try:
        r = m.app.test_client().post(
            "/api/draft-reply",
            data={"token": "zz-draft-token-0123456789",
                  "sender_email": f"{TAG}.p@example.invalid".lower(),
                  "subject": "Availability in June",
                  "body": "Do you have a room for two in June?"})
        s.check("with one it answers rather than raising",
                r.status_code in (200, 400, 429, 503),
                detail=f"status {r.status_code} — the add-in has to be able to "
                       "read whatever comes back")
        s.check("and answers in JSON",
                r.is_json, detail=r.content_type)
    finally:
        m.GUEST_LOOKUP_TOKEN = was
    s.check("and the token is off again",
            m.app.test_client().post(
                "/api/draft-reply",
                data={"token": "zz-draft-token-0123456789"}).status_code == 404)

    s.section("Nothing reached anybody")
    s.check("urlopen is the app's own again",
            getattr(m.urlopen, "__name__", "") != "_refuse",
            detail="left replaced, every later suite would believe the "
                   "network was unreachable")
    s.check("and the accounting block is back in place",
            getattr(m._pennylane_request, "__name__", "") == "_blocked",
            detail="left as the real one, a later suite that wandered into "
                   "Pennylane would reach it rather than be stopped")
    s.check("and the token in .env was never used",
            m.pennylane_configured() is False)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
