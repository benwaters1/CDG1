"""One waitlist offer at a time, with a deadline on it.

When a room came free the house wrote to EVERY matching entry at once and let
them race. Nothing was ever double-booked — claim_range sees to that — but four
of the five people told a room was free were then turned down for it, by the
house that had just written to them. That is worse than not writing.

Three things carry this file.

  ONE AT A TIME, LONGEST-WAITING FIRST. The person who has been on the list
  since March should not lose it to somebody who joined on Tuesday and happened
  to read their email faster.

  THE DEADLINE IS RECORDED WITH THE CONTACT. An offer with no visible expiry is
  no better than no offer: the list has to be able to say when it runs out,
  because that is the moment the next person can be told.

  A LAPSED OFFER GOES BACK ON THE LIST, and is passed to the next — but never
  straight back to the person who let it run out, or a lapse would hand it to
  them again for ever.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZWL"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM waitlist_entries WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _entry(ref, *, arrival, nights=3, joined_days_ago=10):
    conn = db()
    joined = datetime.now(timezone.utc) - timedelta(days=joined_days_ago)
    conn.execute(
        """INSERT INTO waitlist_entries (name, email, phone, desired_arrival,
           desired_departure, party_size, status, created_at)
           VALUES (?, ?, '', ?, ?, 2, 'open', ?)""",
        (f"{TAG} {ref}", f"zzwl.{ref}@example.invalid".lower(),
         arrival.isoformat(), (arrival + timedelta(days=nights)).isoformat(),
         joined.isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM waitlist_entries WHERE name = ?",
                       (f"{TAG} {ref}",)).fetchone()
    conn.close()
    return row


def _row(ref):
    conn = db()
    try:
        return conn.execute("SELECT * FROM waitlist_entries WHERE name = ?",
                            (f"{TAG} {ref}",)).fetchone()
    finally:
        conn.close()


def _autonotify(on=True):
    conn = db()
    was = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'automation_waitlist_autonotify_enabled'"
    ).fetchone()
    conn.execute(
        """INSERT INTO app_settings (key, value) VALUES ('automation_waitlist_autonotify_enabled', ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        ("1" if on else "0",))
    conn.commit()
    conn.close()
    return was["value"] if was else None


def run():
    s = Suite("The waitlist offer")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    was = _autonotify(True)
    sent = []
    was_email, was_sms = m.send_email, m.send_sms
    m.send_email = lambda to, subj, body, **k: (sent.append(to), True)[1]
    m.send_sms = lambda conn, to, body, **k: (True, None)

    try:
        arrival = m.house_today() + timedelta(days=60)
        # Two people want the same nights. One has been waiting since March.
        early = _entry("EARLY", arrival=arrival, joined_days_ago=120)
        late = _entry("LATE", arrival=arrival, joined_days_ago=2)

        s.section("Only one person is told")
        conn = db()
        # The notifier builds the booking URL with _external=True, so it needs
        # an app context. Called from a route it always has one.
        with m.app.test_request_context("/"):
            told = m.notify_room_waitlist_opening(
                conn, arrival.isoformat(), (arrival + timedelta(days=3)).isoformat())
        conn.close()
        s.check("exactly one offer goes out", len(told) == 1,
                detail=f"{len(told)} — writing to everybody meant turning most "
                       "of them down afterwards")
        s.check("and it is the one who has waited longest",
                _row("EARLY")["status"] == "contacted",
                detail="the person on the list since March should not lose it "
                       "to somebody who read their email faster")
        s.check("the other is untouched", _row("LATE")["status"] == "open")

        s.section("The deadline is recorded with it")
        e = _row("EARLY")
        s.check("when it was offered", bool(e["offered_at"]))
        s.check("and when it runs out", bool(e["offer_expires_at"]),
                detail="an offer with no visible expiry is no better than none")
        s.check("which is in the future",
                (e["offer_expires_at"] or "") > datetime.now(timezone.utc).isoformat(),
                detail=f"{e['offer_expires_at']}")
        conn = db()
        s.check("and the window is the house's to set",
                m.waitlist_offer_hours(conn) == 48,
                detail="two days: long enough to answer over a weekend, short "
                       "enough that a room does not sit while one person thinks")
        conn.close()

        s.section("It is on the page, not only in the database")
        page = oc.get("/admin/waitlist").get_data(as_text=True)
        s.check("the list says when it runs out", "theirs until" in page,
                detail="the moment it expires is the moment the next person "
                       "can be told, so somebody has to be able to see it")

        s.section("When it runs out it passes to the next")
        conn = db()
        conn.execute(
            "UPDATE waitlist_entries SET offer_expires_at = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), early["id"]))
        conn.commit()
        with m.app.test_request_context("/"):
            out = m.lapse_waitlist_offers(conn)
        conn.close()
        s.check("the lapse is counted", out["offers lapsed"] >= 1, detail=f"{out}")
        s.check("they go back on the list, not into a state of their own",
                _row("EARLY")["status"] == "open",
                detail="they are still waiting, which is what open means")
        s.check("and the deadline is cleared with it",
                _row("EARLY")["offer_expires_at"] is None,
                detail="a date left behind says an offer is live when it is not")
        s.check("the next person is told",
                _row("LATE")["status"] == "contacted",
                detail=f"{out} — a lapse that tells nobody is a room left unsold")
        # THE ONE THAT WOULD LOOP FOR EVER: handing it straight back to the
        # person who just let it run out.
        s.check("and not the person who let it run out",
                _row("EARLY")["status"] == "open",
                detail="offering it back to them is how an expiry becomes a "
                       "no-op that runs daily")

        # AFTER the lapse, deliberately. Run before it, this section contacts
        # the next person in line itself -- so 'the next person is told' below
        # was already true before the lapse did anything, and the check could
        # not fail. Found by breaking the lapse and watching nothing go red.
        s.section("Nobody is offered the same nights twice over")
        conn = db()
        with m.app.test_request_context("/"):
            again = m.notify_room_waitlist_opening(
                conn, arrival.isoformat(), (arrival + timedelta(days=3)).isoformat())
        conn.close()
        s.check("a second opening does not re-offer to the same person",
                len(again) <= 1 and _row("EARLY")["status"] == "contacted",
                detail=f"{len(again)} — they already hold it")

        s.section("An offer still live is left alone")
        conn = db()
        with m.app.test_request_context("/"):
            quiet = m.lapse_waitlist_offers(conn)
        conn.close()
        s.check("nothing lapses before its time", quiet["offers lapsed"] == 0,
                detail=f"{quiet}")
        s.check("and the live offer stands", _row("LATE")["status"] == "contacted")

        s.section("With the automation off, nothing is offered at all")
        _autonotify(False)
        third = _entry("THIRD", arrival=arrival + timedelta(days=30))
        conn = db()
        with m.app.test_request_context("/"):
            none_told = m.notify_room_waitlist_opening(
                conn, (arrival + timedelta(days=30)).isoformat(),
                (arrival + timedelta(days=33)).isoformat())
        conn.close()
        s.check("the switch still turns it off", none_told == [],
                detail="the caller falls back to telling the owner to work the "
                       "list by hand")
        s.check("and nobody is left holding a phantom offer",
                _row("THIRD")["status"] == "open")
    finally:
        m.send_email, m.send_sms = was_email, was_sms
        _autonotify(was == "1" if was is not None else True)
        _cleanup()
    return s
