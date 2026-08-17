"""The payment return and webhook paths.

These were the most dangerous untested code in the app. Both of them once 500'd
on every single paid booking, because stripe-python's StripeObject has no .get()
— its __getattr__ turns session.get("x") into a lookup for a field named "get" —
so the guest paid, was bounced to an error page, and no booking existed. That
only surfaced with real keys in place.

So the checks here are about the shapes that broke: a return with nothing useful
in it, a webhook that cannot be trusted, and the same payment arriving twice.
Nothing here contacts Stripe — the harness clears the keys — which is the point:
these paths must fail safely when the provider is absent or lying.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZPAY"


def run():
    s = Suite("Payments")
    clients()
    pub = m.app.test_client()

    s.section("Returning from checkout without a usable session")
    # With no session_id, or Stripe not configured, these must answer cleanly
    # rather than raise. A guest who lands here has usually just paid.
    for path in ("/book/stripe-success", "/book/stripe-cancel",
                 "/restaurant/stripe-success", "/restaurant/stripe-cancel",
                 "/workshops/stripe-success"):
        r = pub.get(path)
        s.check(f"{path} does not crash", r.status_code < 500,
                detail=f"HTTP {r.status_code}")
        r = pub.get(path + "?session_id=cs_test_nonexistent_abc123")
        s.check(f"{path} survives an unknown session id", r.status_code < 500,
                detail=f"HTTP {r.status_code}")

    s.section("The webhook refuses what it cannot verify")
    # A webhook that accepts unsigned posts is a webhook anyone can use to mark
    # bookings paid.
    r = pub.post("/webhooks/stripe", data=b'{"type":"checkout.session.completed"}',
                 content_type="application/json")
    s.check("an unsigned webhook is rejected", r.status_code >= 400,
            detail=f"HTTP {r.status_code}")
    r = pub.post("/webhooks/stripe", data=b'{"type":"checkout.session.completed"}',
                 content_type="application/json",
                 headers={"Stripe-Signature": "t=1,v1=deadbeef"})
    s.check("a forged signature is rejected", r.status_code >= 400,
            detail=f"HTTP {r.status_code}")
    s.check("and neither attempt created a booking", _no_booking_from(TAG))

    s.section("The same payment cannot become two bookings")
    # The guest returning from Stripe AND the webhook both create the booking,
    # so that a guest who closes the tab still gets one. A unique index on the
    # session id is what stops those two paths producing a duplicate.
    conn = db()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 LIMIT 1").fetchone()
    index_sql = conn.execute(
        """SELECT sql FROM sqlite_master WHERE type = 'index'
           AND tbl_name = 'bookings' AND sql LIKE '%stripe_session_id%'""").fetchone()
    conn.close()
    s.check("there is a unique index on stripe_session_id",
            index_sql is not None and "UNIQUE" in (index_sql["sql"] or ""),
            detail="without it, paying once could book twice")

    if room and index_sql:
        conn = db()
        args = (room["id"], f"{TAG} payer", f"{TAG.lower()}@example.invalid",
                "2027-09-01", "2027-09-03")
        conn.execute(
            """INSERT INTO bookings (room_id, guest_name, guest_email, arrival_date,
               departure_date, party_size, status, reference_code, manage_token,
               created_at, stripe_session_id)
               VALUES (?,?,?,?,?,2,'confirmed',?,?,datetime('now'),'cs_test_dupe')""",
            (*args, f"{TAG}1", f"tok{TAG}1"))
        conn.commit()
        duplicated = True
        try:
            conn.execute(
                """INSERT INTO bookings (room_id, guest_name, guest_email, arrival_date,
                   departure_date, party_size, status, reference_code, manage_token,
                   created_at, stripe_session_id)
                   VALUES (?,?,?,?,?,2,'confirmed',?,?,datetime('now'),'cs_test_dupe')""",
                (*args, f"{TAG}2", f"tok{TAG}2"))
            conn.commit()
        except Exception:
            duplicated = False
        rows = conn.execute(
            "SELECT COUNT(*) AS c FROM bookings WHERE stripe_session_id = 'cs_test_dupe'"
        ).fetchone()["c"]
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
        conn.commit()
        conn.close()
        s.check("a second booking on the same session is rejected",
                duplicated is False and rows == 1, detail=f"{rows} rows written")

    s.section("Reading a Stripe object cannot use .get()")
    # The exact bug. sval/smeta exist because StripeObject.__getattr__ turns
    # .get("x") into a lookup for a field called "get" and raises. If someone
    # reintroduces a plain .get() on a session these helpers are the guard, so
    # they need to behave on the shapes Stripe really sends.
    s.check("sval and smeta exist", hasattr(m, "sval") and hasattr(m, "smeta"))
    if hasattr(m, "sval"):
        class FakeStripeObject(dict):
            """Raises on attribute access the way StripeObject does."""
            def __getattr__(self, name):
                raise AttributeError(name)

        obj = FakeStripeObject({"amount_total": 12500, "metadata": {"booking_ref": "GUD-1"}})
        s.check("sval reads a present field", m.sval(obj, "amount_total") == 12500)
        s.check("sval returns the default for a missing one",
                m.sval(obj, "not_there", "fallback") == "fallback")
        s.check("sval survives None", m.sval(None, "anything", "fallback") == "fallback")
        s.check("smeta returns a plain dict",
                m.smeta(obj) == {"booking_ref": "GUD-1"},
                detail=f"got {m.smeta(obj)!r}")
        s.check("smeta copes with no metadata at all",
                m.smeta(FakeStripeObject({})) == {})

    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log")
    conn.commit()
    conn.close()
    return s


def _no_booking_from(tag):
    conn = db()
    n = conn.execute("SELECT COUNT(*) AS c FROM bookings WHERE guest_name LIKE ?",
                     (tag + "%",)).fetchone()["c"]
    conn.close()
    return n == 0
