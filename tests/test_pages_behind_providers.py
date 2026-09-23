"""Four pages that need Stripe or the model to answer, answered without either.

They sat on COVERAGE_KNOWN_GAPS, each with a good reason: reaching the branch
that matters "means a real payment provider", and nothing here is ever
allowed near the château's own Stripe account or its model bill. So their
working branches — the ones that move money and record it — had never once
run under test.

They run now, with the provider STOOD IN at the one call that would leave the
building, restored in a `finally`, and checked restored. Nothing here can
reach the real thing even by accident: _harness blanks stripe.api_key and the
Anthropic key at import and asserts both, so a stand-in that leaked would hit
a library with no credential and be refused before it sent a byte.

What each one is really guarding:

  A REFUND WRITES NOTHING UNLESS THE MONEY SIDE SUCCEEDED. A refund recorded
  when Stripe declined it is a guest told they have been paid back who has
  not. And one settled outside the system — a bank transfer, cash — is
  recorded without Stripe being asked at all, which is its own working branch
  and the one used most.

  A PAYMENT RETURN PAGE BELIEVES STRIPE, NOT THE URL. Anybody can type
  ?session_id=anything onto the success address. The page asks Stripe what
  that session is, and only a session Stripe says is paid marks anything.
  And a guest who reloads it after paying must not be counted twice — that
  takes the money off what they owe twice and sends them home thinking they
  have paid.

  A DRAFTED REPLY IS ONLY EVER A DRAFT. The add-in's button returns words for
  a person to read and edit. It must never send anything itself.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZPROV"
_MISSING = object()


class _standin:
    """Replace one attribute for the length of a `with`, then put it back.

    Restores the RAW class attribute where there is one (a classmethod on a
    Stripe resource), and deletes an instance attribute that was not there
    before, so what is left afterwards is exactly what was there.
    """

    def __init__(self, owner, name, value):
        self.owner, self.name, self.value = owner, name, value

    def __enter__(self):
        self.raw = vars(self.owner).get(self.name, _MISSING) if hasattr(self.owner, "__dict__") else _MISSING
        setattr(self.owner, self.name, self.value)
        return self

    def __exit__(self, *exc):
        if self.raw is _MISSING:
            try:
                delattr(self.owner, self.name)
            except AttributeError:
                pass
        else:
            setattr(self.owner, self.name, self.raw)
        return False


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM refunds WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_transactions WHERE workshop_booking_id IN "
                 "(SELECT id FROM workshop_bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes = ?", (TAG,))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM booking_payments WHERE stripe_session_id LIKE 'cs_test_zzprov%'")
    conn.execute("DELETE FROM booking_shares WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _dinner(total, intent=None):
    conn = db()
    ref = "DIN-ZZ" + m.secrets.token_hex(3).upper()
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
             guest_email, dinner_date, party_size, status, payment_status,
             total_price, stripe_payment_intent_id, created_at)
           VALUES (?, ?, ?, 'zzprov@example.invalid', ?, 2, 'confirmed', 'paid', ?, ?, ?)""",
        (ref, "zzprov" + m.secrets.token_hex(6), TAG + " diner",
         (m.house_today() + timedelta(days=20)).isoformat(), total, intent, _now()))
    rid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    conn.close()
    return rid


def _refunds(rid):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM refunds WHERE category = 'restaurant' AND booking_id = ? "
            "ORDER BY id", (rid,)).fetchall()
    finally:
        conn.close()


def _status(table, rid):
    conn = db()
    try:
        return conn.execute(f"SELECT payment_status FROM {table} WHERE id = ?",
                            (rid,)).fetchone()["payment_status"]
    finally:
        conn.close()


def _atelier_place():
    conn = db()
    start = m.house_today() + timedelta(days=90)
    conn.execute("INSERT INTO workshops (title, description, price_per_person, active, "
                 "created_at) VALUES (?, 'x', 600, 1, ?)", (TAG + " atelier", _now()))
    wid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute("INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, "
                 "notes, created_at) VALUES (?, ?, ?, 8, ?, ?)",
                 (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), TAG, _now()))
    sid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    token = "zzprov" + m.secrets.token_hex(6)
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
             guest_name, guest_email, party_size, status, created_at)
           VALUES (?, ?, ?, ?, 'zzprov.atelier@example.invalid', 1, 'confirmed', ?)""",
        (sid, m.make_workshop_reference_code(), token, TAG + " maker", _now()))
    bid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    conn.close()
    return bid, token


def _deposit(bid):
    conn = db()
    try:
        row = conn.execute("SELECT deposit_paid_at FROM workshop_bookings WHERE id = ?",
                           (bid,)).fetchone()
        paid = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM workshop_transactions "
            "WHERE workshop_booking_id = ? AND kind = 'payment'", (bid,)).fetchone()
        return row["deposit_paid_at"], paid["n"], paid["total"]
    finally:
        conn.close()


def run():
    s = Suite("Pages behind Stripe and the model, answered without either")
    _cleanup()
    oc, ec, _owner, _emp = clients()

    s.section("Nothing here can reach the real thing")
    s.check("Stripe is off", not m.stripe_enabled())
    s.check("and has no key to use", not getattr(m.stripe, "api_key", None),
            detail="a stand-in that leaked would meet a library with no "
                   "credential, which refuses before it sends anything")
    s.check("the model is not configured", not m.claude_configured())

    # ------------------------------------------------------------------
    s.section("A refund settled outside the system")
    rid = _dinner(90.0)
    r = oc.post(f"/admin/restaurant/{rid}/refund",
                data={"amount": "30", "reason": TAG + " the soufflé fell",
                      "method": "bank_transfer"})
    rows = _refunds(rid)
    s.check("the route answers", r.status_code in (302, 303), detail=f"HTTP {r.status_code}")
    s.check("and records the refund", len(rows) == 1 and rows[0]["amount"] == 30.0,
            detail=f"{[dict(x) for x in rows]}")
    s.check("as money that moved outside, with no Stripe refund against it",
            rows and rows[0]["method"] == "bank_transfer" and not rows[0]["stripe_refund_id"])
    s.check("a part refund leaves the dinner paid, not refunded",
            _status("restaurant_bookings", rid) == "paid",
            detail="the real figure lives in the refunds table")
    oc.post(f"/admin/restaurant/{rid}/refund",
            data={"amount": "", "reason": TAG + " the rest", "method": "bank_transfer"})
    s.check("a blank amount refunds what is left, and then it is refunded",
            [x["amount"] for x in _refunds(rid)] == [30.0, 60.0]
            and _status("restaurant_bookings", rid) == "refunded",
            detail=f"{[x['amount'] for x in _refunds(rid)]} / "
                   f"{_status('restaurant_bookings', rid)}")
    oc.post(f"/admin/restaurant/{rid}/refund",
            data={"amount": "10", "reason": TAG + " once more", "method": "bank_transfer"})
    s.check("and nothing more can be refunded than was paid",
            len(_refunds(rid)) == 2,
            detail="refunding past what was paid invents money in the record")
    rid2 = _dinner(50.0)
    oc.post(f"/admin/restaurant/{rid2}/refund",
            data={"amount": "10", "reason": "", "method": "bank_transfer"})
    s.check("a refund with no reason is refused", not _refunds(rid2))
    # Found by this file: the first fixture said "bank", which the refunds table
    # does not take, and the route answered 500 rather than a sentence.
    odd = oc.post(f"/admin/restaurant/{rid2}/refund",
                  data={"amount": "10", "reason": TAG + " odd", "method": "bank"})
    s.check("a method the house does not use is refused, not a 500",
            odd.status_code in (302, 303) and not _refunds(rid2),
            detail=f"HTTP {odd.status_code}")
    s.check("an employee cannot refund",
            ec.post(f"/admin/restaurant/{rid2}/refund",
                    data={"amount": "5", "reason": TAG + " x", "method": "bank_transfer"}).status_code
            not in (200,) and not _refunds(rid2))

    # ------------------------------------------------------------------
    s.section("A card refund, with Stripe stood in")
    # What is there BEFORE, so "put back" can be checked as identity rather
    # than as "Stripe still reads as off", which would pass with the fake left
    # in place.
    raw_refund_create = vars(m.stripe.Refund).get("create", _MISSING)
    rid3 = _dinner(45.50, intent="pi_test_zzprov")
    asked = []

    class _Refund:
        id = "re_test_zzprov"

    def _create(**kwargs):
        asked.append(kwargs)
        return _Refund()

    with _standin(m, "stripe_enabled", lambda: True), \
            _standin(m.stripe.Refund, "create", staticmethod(_create)):
        oc.post(f"/admin/restaurant/{rid3}/refund",
                data={"amount": "45.50", "reason": TAG + " by card", "method": "stripe"})
    s.check("Stripe was asked exactly once", len(asked) == 1, detail=str(asked))
    s.check("for the right payment", asked and asked[0].get("payment_intent") == "pi_test_zzprov")
    s.check("in cents — €45.50 is 4550, not 45",
            asked and asked[0].get("amount") == 4550,
            detail=f"{asked[0].get('amount') if asked else None} — Stripe works in "
                   "the smallest unit, and 45 would refund 45 cents")
    s.check("with a key that makes a double-click the same refund",
            asked and str(rid3) in (asked[0].get("idempotency_key") or ""),
            detail=f"{asked[0].get('idempotency_key') if asked else None}")
    s.check("and Stripe's refund is written down against it",
            [x["stripe_refund_id"] for x in _refunds(rid3)] == ["re_test_zzprov"])

    rid4 = _dinner(80.0, intent="pi_test_zzprov_declined")

    def _decline(**kwargs):
        raise RuntimeError("Your card was declined for a refund")

    with _standin(m, "stripe_enabled", lambda: True), \
            _standin(m.stripe.Refund, "create", staticmethod(_decline)):
        oc.post(f"/admin/restaurant/{rid4}/refund",
                data={"amount": "80", "reason": TAG + " declined", "method": "stripe"})
    s.check("a refund Stripe refused writes nothing",
            not _refunds(rid4) and _status("restaurant_bookings", rid4) == "paid",
            detail="recorded anyway, it is a guest told they have been paid "
                   "back who has not")
    s.check("and the stand-ins are put back, exactly",
            not m.stripe_enabled()
            and vars(m.stripe.Refund).get("create", _MISSING) is raw_refund_create,
            detail="left in place, every suite after this one could refund")

    # ------------------------------------------------------------------
    s.section("The atelier deposit return page")
    bid, tok = _atelier_place()
    sessions = {
        "cs_test_zzprov_dep": {"id": "cs_test_zzprov_dep", "payment_status": "paid",
                               "amount_total": 30000,
                               "metadata": {"kind": "workshop_deposit",
                                            "workshop_booking_id": str(bid)}},
        "cs_test_zzprov_open": {"id": "cs_test_zzprov_open", "payment_status": "unpaid",
                                "amount_total": 30000,
                                "metadata": {"kind": "workshop_deposit",
                                             "workshop_booking_id": str(bid)}},
    }
    looked_up = []

    def _retrieve(session_id, *a, **k):
        looked_up.append(session_id)
        if session_id not in sessions:
            raise RuntimeError("No such checkout.session")
        return sessions[session_id]

    anon = m.app.test_client()
    url = "/workshops/stripe-success?manage_token=%s&kind=deposit&session_id=%s"
    with _standin(m, "stripe_enabled", lambda: True), \
            _standin(m.stripe.checkout.Session, "retrieve", staticmethod(_retrieve)):
        open_ = anon.get(url % (tok, "cs_test_zzprov_open"))
        forged = anon.get(url % (tok, "cs_i_made_this_up"))
        at_first = _deposit(bid)
        paid = anon.get(url % (tok, "cs_test_zzprov_dep"))
        after_pay = _deposit(bid)
        again = anon.get(url % (tok, "cs_test_zzprov_dep"))
        after_reload = _deposit(bid)
    s.check("an unpaid session marks nothing", at_first[0] is None and at_first[1] == 0,
            detail=f"{at_first}")
    s.check("a session Stripe has never heard of is a 404", forged.status_code == 404,
            detail=f"HTTP {forged.status_code} — anybody can type a session id "
                   "onto this address")
    s.check("the page asked Stripe rather than believing the address",
            "cs_i_made_this_up" in looked_up, detail=str(looked_up))
    s.check("a paid session answers and goes to the guest's page",
            paid.status_code in (302, 303) and tok in (paid.headers.get("Location") or ""),
            detail=f"HTTP {paid.status_code} -> {paid.headers.get('Location')}")
    s.check("and marks the deposit paid", after_pay[0] is not None and after_pay[1] == 1,
            detail=f"{after_pay}")
    s.check("for the amount Stripe took", after_pay[2] == 300.0, detail=f"{after_pay[2]}")
    s.check("and reloading the page does not count it twice",
            after_reload[1] == 1 and after_reload[2] == 300.0,
            detail=f"{after_reload} — counted twice, it comes off what they "
                   "owe twice")

    # ------------------------------------------------------------------
    s.section("The split-bill return page")
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    conn.close()
    arrival = _harness.free_window(room["id"], 2, after_days=760, clear_of_ateliers=True)
    conn = db()
    with m.app.test_request_context("/"):
        ref, _mt = m.create_booking(conn, room, TAG + " party", "zzprov.party@example.invalid",
                                    "", arrival, arrival + timedelta(days=2), 2, "", [],
                                    payment_status="unpaid")
    conn.commit()
    booking = conn.execute("SELECT * FROM bookings WHERE reference_code = ?", (ref,)).fetchone()
    conn.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (booking["id"],))
    conn.commit()
    share, err = m.create_booking_share(conn, booking["id"], TAG + " friend",
                                        "zzprov.friend@example.invalid", 150)
    conn.commit()
    conn.close()
    s.check("a share of the bill was made to pay", share is not None, detail=str(err))
    if share is not None:
        sessions["cs_test_zzprov_share"] = {
            "id": "cs_test_zzprov_share", "payment_status": "paid", "amount_total": 15000,
            "metadata": {"kind": "room_balance", "booking_id": str(booking["id"]),
                         "share_id": str(share["id"])}}
        share_url = "/book/share-paid?token=%s&session_id=cs_test_zzprov_share" % share["token"]
        with _standin(m, "stripe_enabled", lambda: True), \
                _standin(m.stripe.checkout.Session, "retrieve", staticmethod(_retrieve)):
            first = anon.get(share_url)
            anon.get(share_url)
        conn = db()
        payments = conn.execute("SELECT amount FROM booking_payments WHERE stripe_session_id = ?",
                                ("cs_test_zzprov_share",)).fetchall()
        share_now = conn.execute("SELECT status FROM booking_shares WHERE id = ?",
                                 (share["id"],)).fetchone()
        conn.close()
        s.check("the page answers and goes back to the share",
                first.status_code in (302, 303), detail=f"HTTP {first.status_code}")
        s.check("the share is paid", share_now and share_now["status"] == "paid",
                detail=f"{share_now['status'] if share_now else None}")
        s.check("and the payment is recorded once, however often the page is reloaded",
                [p["amount"] for p in payments] == [150.0],
                detail=f"{[p['amount'] for p in payments]}")

    # ------------------------------------------------------------------
    s.section("A drafted reply for the Outlook add-in")
    sent = []
    with _standin(m, "GUEST_LOOKUP_TOKEN", "zz-addin-test-token"), \
            _standin(m, "send_email", lambda *a, **k: sent.append((a, k)) or True):
        nope = anon.post("/api/draft-reply", data={"token": "wrong"})
        unconfigured = anon.post("/api/draft-reply", data={"token": "zz-addin-test-token"})
        seen = []
        with _standin(m, "claude_configured", lambda: True), \
                _standin(m, "draft_reply_with_claude",
                         lambda ctx, text: seen.append((ctx, text)) or "Dear Margot, of course."):
            drafted = anon.post("/api/draft-reply", data={
                "token": "zz-addin-test-token", "recipient_email": "zzprov.guest@example.invalid",
                "body": "Can we move our dinner to Saturday?"})
        with _standin(m, "claude_configured", lambda: True), \
                _standin(m, "draft_reply_with_claude", lambda ctx, text: None):
            failed = anon.post("/api/draft-reply", data={"token": "zz-addin-test-token"})
    s.check("a wrong token is a 404, like the add-in's other endpoints",
            nope.status_code == 404, detail=f"HTTP {nope.status_code}")
    s.check("with no model it says so rather than failing",
            unconfigured.status_code == 503, detail=f"HTTP {unconfigured.status_code}")
    got = drafted.get_json(silent=True) or {}
    s.check("with one, it answers with a draft", drafted.status_code == 200
            and got.get("draft") == "Dear Margot, of course.",
            detail=f"HTTP {drafted.status_code} {got}")
    s.check("written from what the person typed",
            seen and seen[0][1] == "Can we move our dinner to Saturday?",
            detail=str(seen[:1])[:120])
    s.check("and it sent nothing — a draft is for a person to read",
            not sent, detail=f"{len(sent)} email(s) — the add-in only ever offers it")
    s.check("a draft that does not come back is said, not faked",
            failed.status_code == 502, detail=f"HTTP {failed.status_code}")
    s.check("and the add-in token is put back",
            m.GUEST_LOOKUP_TOKEN != "zz-addin-test-token")

    _cleanup()
    return s
