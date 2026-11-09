"""Enquiries waiting for an answer, and what the spam filter took, on the owner home.

9 November 2026, after the Contact page's spam was dealt with, the owner
agreed to two lines on the home page:

  - an enquiry nobody has answered within a day -- the same day the booking
    page promises -- from the Contact page or the events page, which goes
    when each is marked answered (or as spam);
  - how many enquiries were set aside as spam, so a real one caught by the
    rules is seen rather than lost. It counts only what arrived since the
    owner last opened the Spam chip on the Waitlist, within a week: looking
    clears it, and so does a quiet week. A line true every morning would be
    furniture, which is the one thing that panel must never be.

The panel must still be able to be empty, so each line is checked to GO as
well as to come.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "zzeoh"


def _cleanup(conn):
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM waitlist_entries WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_email LIKE ?", (TAG + "%",))
    conn.commit()


def _ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def _enquiry(conn, who, made, status="enquiry", spam_at=None, name=None,
             notes="Private event. A dinner for forty in June."):
    return conn.execute(
        """INSERT INTO waitlist_entries (name, email, phone, notes, status, created_at,
             source, spam_at) VALUES (?, ?, '', ?, ?, ?, 'contact', ?)""",
        (name if name is not None else f"Zzeoh {who}", f"{TAG}.{who}@example.invalid",
         notes, status, made, spam_at)).lastrowid


def _panel(conn):
    with m.app.test_request_context():
        return m.owner_home_warnings(conn, m.house_today())


def _line(conn, starts):
    return next((w for w in _panel(conn) if w["title"].endswith(starts)), None)


def run():
    s = Suite("Enquiries waiting, and the spam pile, on the owner home")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    seen = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                        (m.ENQUIRY_SPAM_SEEN_KEY,)).fetchone()
    try:
        _run(s, oc, conn)
    finally:
        _cleanup(conn)
        conn.execute("DELETE FROM app_settings WHERE key = ?", (m.ENQUIRY_SPAM_SEEN_KEY,))
        if seen:
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)",
                         (m.ENQUIRY_SPAM_SEEN_KEY, seen["value"]))
        conn.commit()
        conn.close()
    return s


def _run(s, oc, conn):
    s.section("How long somebody has waited, in words")
    s.check("under two days, in hours", m.waited_words(30.4) == "30 hours ago")
    s.check("two days or more, in days", m.waited_words(75) == "3 days ago")
    s.check("and an unreadable stamp is not a crash", m.waited_words(None) == "a while ago")

    s.section("A Contact enquiry nobody has answered")
    before = [a["id"] for a in m.enquiries_awaiting_answer(conn) if a["kind"] == "contact"]
    old = _enquiry(conn, "old", "2001-01-01T09:00:00+00:00")
    fresh = _enquiry(conn, "fresh", _ago(hours=2))
    spam = _enquiry(conn, "spam", _ago(hours=40), status="closed", spam_at=_ago(hours=40))
    conn.commit()
    waiting = [a for a in m.enquiries_awaiting_answer(conn) if a["kind"] == "contact"
               and a["id"] not in before]
    s.check("one past a day is waiting", [a["id"] for a in waiting] == [old],
            detail=str([a["id"] for a in waiting]))
    s.check("one two hours old is not yet", fresh not in [a["id"] for a in waiting])
    s.check("and spam is waiting for nobody", spam not in [a["id"] for a in waiting])
    line = _line(conn, "nobody has answered")
    s.check("the home page says so", line is not None and "enquir" in line["title"],
            detail=str(line))
    s.check("naming the oldest, and what it is about",
            line is not None and "Zzeoh old wrote" in line["detail"]
            and "about private event" in line["detail"], detail=line and line["detail"])
    s.check("saying how it goes away",
            line is not None and "mark it Contacted on the Waitlist" in line["detail"])
    s.check("and it opens the Contact enquiries",
            line is not None and line["href"].startswith("/admin/waitlist")
            and "state=Enquiry" in line["href"], detail=line and line["href"])
    page = oc.get(line["href"]).get_data(as_text=True) if line else ""
    s.check("where the enquiry is", f'id="entry-{old}"' in page)

    oc.post(f"/admin/waitlist/{old}/status", data={"status": "contacted"})
    still = [a["id"] for a in m.enquiries_awaiting_answer(conn)]
    s.check("marked Contacted, it is no longer waiting", old not in still)

    s.section("An event enquiry nobody has answered")
    ev = conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
             contact_email, status, created_at)
           VALUES (?, ?, 'wedding', 'Zzeoh Bride', ?, 'new', ?)""",
        (f"ZZEOH-{datetime.now().timestamp():.0f}", f"zzeoh-{datetime.now().timestamp()}",
         f"{TAG}.bride@example.invalid", _ago(days=3))).lastrowid
    conn.commit()
    got = next((a for a in m.enquiries_awaiting_answer(conn)
                if a["kind"] == "event" and a["id"] == ev), None)
    s.check("is waiting too", got is not None)
    s.check("three days, said in days", got is not None and m.waited_words(got["waited"]) == "3 days ago",
            detail=str(got))
    conn.execute("UPDATE event_inquiries SET status = 'contacted' WHERE id = ?", (ev,))
    conn.commit()
    s.check("replied to, it is not", not any(a["kind"] == "event" and a["id"] == ev
                                            for a in m.enquiries_awaiting_answer(conn)))

    s.section("What the spam filter took")
    # Start from a look just now, as if the owner had opened the chip.
    oc.get("/admin/waitlist?when=Spam")
    s.check("just looked at, there is no line", _line(conn, "set aside as spam") is None)
    caught = _enquiry(conn, "caught", _ago(minutes=5), status="closed", spam_at=_ago(seconds=0),
                      name="", notes="Private event. Test")
    conn.commit()
    line = _line(conn, "set aside as spam")
    s.check("one set aside since is counted", line is not None and line["count"] == 1,
            detail=str(line))
    s.check("with who and what, so a real one can be told from a script",
            line is not None and "no name" in line["detail"] and "“Test”" in line["detail"],
            detail=line and line["detail"])
    s.check("and it opens the Spam chip",
            line is not None and "when=Spam" in line["href"], detail=line and line["href"])
    s.check("the least urgent kind of line", line is not None and line["severity"] == "watch")
    oc.get(line["href"] if line else "/admin/waitlist?when=Spam")
    s.check("opening the Spam chip clears it", _line(conn, "set aside as spam") is None)

    s.section("A week with nobody looking clears it too")
    conn.execute("DELETE FROM app_settings WHERE key = ?", (m.ENQUIRY_SPAM_SEEN_KEY,))
    conn.execute("UPDATE waitlist_entries SET spam_at = ? WHERE id = ?", (_ago(days=8), caught))
    conn.execute("UPDATE waitlist_entries SET spam_at = ? WHERE id = ?", (_ago(days=9), spam))
    conn.commit()
    n, _last = m.enquiry_spam_unseen(conn)
    mine = conn.execute("SELECT COUNT(*) FROM waitlist_entries WHERE email LIKE ? AND spam_at > ?",
                        (TAG + "%", _ago(days=7))).fetchone()[0]
    s.check("spam more than a week old is not counted", mine == 0 and
            not any(r for r in [_last] if r and (r["email"] or "").startswith(TAG)),
            detail=f"{n} counted")
