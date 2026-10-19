"""The daily summary and the morning note go to the technical address.

The owner's sign-in address is accounts@, and accounts@ is money only. The
15 Oct 2026 brief kept "Your daily summary" with the owner; the owner said on
19 Oct that was wrong for this house: the summaries go to website@, with the
backups, and accounts@ keeps the invoices.

What this holds:
  - with the technical address set, the nightly summary job, the morning note
    and the scheduler's /api/owner-digest each send to it;
  - with it empty, each falls back to the owner, as the backups do, so an
    empty box never means no summary;
  - the Guest email page's table says the summaries go to the technical
    address, worked out by the helper the senders call, and the hint under
    the box names the daily summary;
  - money that needs the owner, and a poor review, still go to the owner and
    never ask the technical address.

Every send is a stand-in. Nothing reaches a provider.
"""
import inspect

import _harness
from _harness import Suite

import app as m

TECH = "website.summary@example.invalid"
TOKEN = "zzsum-digest-token"


def _set(conn, value):
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (m.TECH_EMAIL_KEY, value))
    conn.commit()


def _sent_by(fn):
    """Run fn with send_email stood in. Returns [(to, subject)]."""
    sent = []
    was = m.send_email

    def fake(to, subject, body, *a, **k):
        sent.append((to, subject))
        return True
    m.send_email = fake
    try:
        fn()
    finally:
        m.send_email = was
    return sent


def _nightly(conn):
    with m.app.test_request_context("/"):
        return _sent_by(lambda: m.run_daily_digest_job(conn))


def _morning(conn):
    # A morning with something in it, whatever the database holds: the note's
    # contents are test_digest_and_hub's; this is only about where it goes.
    was = m.morning_digest
    m.morning_digest = lambda conn, day: ("Today at the house", "Two arriving.", True)
    try:
        with m.app.test_request_context("/"):
            return _sent_by(lambda: m.run_morning_digest_job(conn))
    finally:
        m.morning_digest = was


def _scheduler():
    was = m.DIGEST_TOKEN
    m.DIGEST_TOKEN = TOKEN
    try:
        out = {}

        def call():
            r = m.app.test_client().get(f"/api/owner-digest?token={TOKEN}")
            out["status"], out["json"] = r.status_code, r.get_json(silent=True)
        sent = _sent_by(call)
        return sent, out
    finally:
        m.DIGEST_TOKEN = was


def run():
    s = Suite("the daily summary goes to the technical address")
    conn = m.get_db()
    put_back = _harness.keep_rows(conn, "app_settings", "key = ?", (m.TECH_EMAIL_KEY,))
    try:
        _checks(s, conn)
    finally:
        put_back()
        conn.close()
    return s


def _checks(s, conn):
    oc, _ec, _owner, _emp = _harness.clients()
    owner_to = m.owner_email(conn)
    s.check("there is an owner address to fall back to", bool(owner_to))

    s.section("With the technical address set")
    _set(conn, TECH)
    sent = _nightly(conn)
    s.check("the nightly summary goes to it", sent == [(TECH, "Your daily summary")],
            detail=f"{sent} -- accounts@ is money only")
    sent = _morning(conn)
    s.check("so does the morning note", [to for to, _ in sent] == [TECH], detail=str(sent))
    sent, out = _scheduler()
    s.check("and the scheduler's summary", out.get("status") == 200
            and [to for to, _ in sent] == [TECH] and (out.get("json") or {}).get("to") == TECH,
            detail=f"{sent} {out}")
    s.check("none of them to the owner's sign-in address",
            owner_to not in [to for to, _ in _nightly(conn) + _morning(conn)],
            detail=f"owner is {owner_to!r}")

    s.section("With it empty, each comes to the owner rather than nowhere")
    _set(conn, "")
    sent = _nightly(conn)
    s.check("the nightly summary", sent == [(owner_to, "Your daily summary")], detail=str(sent))
    sent = _morning(conn)
    s.check("the morning note", [to for to, _ in sent] == [owner_to], detail=str(sent))
    sent, out = _scheduler()
    s.check("the scheduler's summary", [to for to, _ in sent] == [owner_to], detail=str(sent))

    s.section("The Guest email page says so")
    _set(conn, TECH)
    rows = dict(m.email_health(conn)["house_mail"])
    s.check("the summaries are shown going to the technical address",
            rows.get("The daily summary and the morning note") == TECH, detail=str(rows))
    s.check("money that needs the owner is still shown with the owner",
            rows.get("Money that needs you: a balance not taken, a paid booking that clashes")
            == owner_to, detail=str(rows))
    s.check("and a poor review", rows.get("A poor review") == owner_to, detail=str(rows))
    html = oc.get("/admin/emails").get_data(as_text=True)
    text = _harness.visible_text(html)
    s.check("the hint under the box names the daily summary",
            "Backups, the provider test and the daily summary." in text)
    s.check("and the words over the table no longer keep it with the owner",
            "and the daily summary, come to you" not in text)

    # The table is a claim about the senders. Hold each to it from the source,
    # so a sender moved without the table, or the table without the sender,
    # is red.
    s.section("Each sender asks the helper the table says")
    for fn in ("run_daily_digest_job", "run_morning_digest_job", "api_owner_digest"):
        src = inspect.getsource(getattr(m, fn))
        s.check(f"{fn} asks tech_email", "tech_email(conn)" in src
                and "owner_email(" not in src)
    s.check("the table says the summaries are technical",
            ("The daily summary and the morning note", "tech") in m.HOUSE_MAIL_ROUTES)
    for fn in ("send_autocharge_failed_email", "create_booking_from_stripe_session",
               "create_restaurant_booking_from_stripe_session",
               "tell_the_house_about_a_review"):
        src = inspect.getsource(getattr(m, fn))
        s.check(f"{fn} still writes to the owner", "owner_email(conn)" in src
                and "tech_email" not in src)
