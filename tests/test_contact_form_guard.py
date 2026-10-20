"""The Contact page's two forms, and the machines that post to them.

19 October 2026. Two entries on the room waitlist, both Open: a "rough month"
of 1970-05 with a phone number in the notes, and "Robertlic" asking in
Albanian what the price is -- a campaign that writes the same line to every
contact form it finds. Each made a task on the owner's day and a letter to
reservations@, and sat on the waitlist as somebody waiting for nights.

Held here:
  - a honeypot filled, a form sent too fast, or a post with no drawn-at stamp
    (or a made-up one) saves NOTHING: no waitlist row, no task, no letter --
    and is thanked exactly as a real enquiry is, so a bot cannot tell;
  - a real enquiry waits as 'enquiry', not 'open', so the letter that offers
    a freed room never goes to somebody who only mentioned a month -- while
    "tell me when that month opens", which asked for that letter, stays open;
  - a month nobody can come in (1970-05, 2099-13, three years out) is turned
    away the same quiet way; this month and the furthest one allowed are not;
  - the waitlist page keeps a Contact enquiry as Enquiry, never Open;
  - the table's CHECK is widened on an old database without losing a row,
    an id, the id counter or the index;
  - Turnstile does nothing without both keys, and with them asks Cloudflare
    (stood in here) -- and lets a guest through when Cloudflare cannot answer;
  - both form templates call the guard, because a handover that dropped it
    would turn away every enquiry, quietly, by design.
"""
import os
import re
import sqlite3
import tempfile
import time
from datetime import timedelta

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "zzcfg"
THANKS = "Thank you. Your message has reached the château, and we will write back."


def _addr(who):
    return f"{TAG}.{who}@example.invalid"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", ("%" + TAG + "%",))
    conn.execute("DELETE FROM waitlist_entries WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action IN "
                 "('contact_enquiry', 'contact_enquiry:bot')")
    conn.commit()
    conn.close()


def _unthrottle():
    """Five enquiries an hour from one address is the limit, and this suite
    sends more than five real ones."""
    conn = db()
    conn.execute("DELETE FROM submission_log WHERE action = 'contact_enquiry'")
    conn.commit()
    conn.close()


def _post(anon, data):
    """(response, letters to the house) for one post to /contact."""
    _unthrottle()
    sent = []
    real = m.send_email
    m.send_email = lambda to, subject, body, *a, **kw: sent.append((to, subject)) or True
    try:
        r = anon.post("/contact", data=data, follow_redirects=True)
    finally:
        m.send_email = real
    return r, sent


def _rough(who, guard=None, **over):
    data = {"action": "flexible", "email": _addr(who), "name": f"Guest {TAG} {who}",
            "rough_month": "", "rough_nights": "3", "rough_guests": "2",
            "flexibility": "week", "message": "Two of us, a quiet week."}
    data.update(_harness.contact_form_fields() if guard is None else guard)
    data.update(over)
    return data


def _saved(who):
    """(entry, task) the app kept for this address, if any."""
    conn = db()
    try:
        entry = conn.execute("SELECT * FROM waitlist_entries WHERE email = ? ORDER BY id DESC",
                             (_addr(who),)).fetchone()
        task = conn.execute("SELECT * FROM tasks WHERE title LIKE ?",
                            ("%" + _addr(who) + "%",)).fetchone()
        return entry, task
    finally:
        conn.close()


def _month_shift(ym, months):
    y, mo = int(ym[:4]), int(ym[5:7])
    n = y * 12 + mo - 1 + months
    return "%04d-%02d" % (n // 12, n % 12 + 1)


def run():
    s = Suite("Contact form guard")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    blocked_verify = m.turnstile_siteverify
    # The harness sets the minimum to nothing for suites that post a form
    # straight back; this one is about the minimum, so it is the real one.
    min_was, m.PUBLIC_FORM_MIN_SECONDS = m.PUBLIC_FORM_MIN_SECONDS, 4
    try:
        s.section("A real enquiry")
        r, letters = _post(anon, _rough("real"))
        entry, task = _saved("real")
        s.check("is kept", entry is not None, detail="; ".join(flashes(r)))
        s.check("as an enquiry, not as somebody waiting for nights",
                entry is not None and entry["status"] == "enquiry",
                detail=str(entry and entry["status"]))
        s.check("with its task", task is not None)
        s.check("and the house is written to",
                [x for x in letters if x[1].startswith("Someone wrote to us")],
                detail=str(letters))
        s.check("and the guest is thanked", flashes(r) == [THANKS], detail=str(flashes(r)))
        real_reply = (r.status_code, r.request.path, flashes(r))

        s.section("What a machine sends is thanked the same, and nothing is kept")
        now = time.time()
        hp, st = m.BOT_HONEYPOT_FIELD, m.BOT_STAMP_FIELD
        machines = [
            ("honeypot", "the hidden field filled in",
             {hp: "https://cheap-seo.example", st: _harness.form_stamp(60)}),
            ("fast", "sent a second after the form was drawn",
             {hp: "", st: _harness.form_stamp(1)}),
            ("nostamp", "posted without ever drawing the form", {}),
            ("forged", "a drawn-at stamp it made up",
             {hp: "", st: "%d.%s" % (now - 600, "0" * 24)}),
        ]
        for who, what, guard in machines:
            r, letters = _post(anon, _rough(who, guard=guard))
            entry, task = _saved(who)
            s.check(f"{what}: no waitlist entry", entry is None,
                    detail=str(entry and dict(entry)))
            s.check(f"{what}: no task", task is None)
            s.check(f"{what}: no letter", not letters, detail=str(letters))
            s.check(f"{what}: the same reply as a real enquiry",
                    (r.status_code, r.request.path, flashes(r)) == real_reply,
                    detail=str((r.status_code, r.request.path, flashes(r))))
        conn = db()
        s.check("and nothing of them counts against a real guest's limit",
                conn.execute("SELECT COUNT(*) FROM submission_log "
                             "WHERE action = 'contact_enquiry'").fetchone()[0] == 0)
        s.check("but each is noted where the other forms note theirs",
                conn.execute("SELECT COUNT(*) FROM submission_log "
                             "WHERE action = 'contact_enquiry:bot'").fetchone()[0] == len(machines))
        conn.close()
        # One clock reading for both the stamp and the judging of it: `now`
        # above was read before four posts, and a stamp made later from the
        # live clock is younger than it by however long they took.
        ts = int(time.time())
        edge = f"{ts - m.PUBLIC_FORM_MIN_SECONDS}.{m._form_stamp_signature(str(ts - m.PUBLIC_FORM_MIN_SECONDS))}"
        s.check("a stamp exactly old enough passes",
                not m.contact_post_refusal({hp: "", st: edge}, now=ts))
        s.check("and one a second younger does not",
                bool(m.contact_post_refusal({hp: "", st: edge}, now=ts - 1)))
        s.check("and the booking forms still let a stampless post through",
                m.bot_guard_refusal({hp: ""}) is None,
                detail="a page drawn before the check existed; only Contact refuses one")
        r, letters = _post(anon, _rough("badaddr", guard={}, email="not-an-address"))
        s.check("a machine with a bad address hears what a person would",
                any("does not look right" in f for f in flashes(r)), detail=str(flashes(r)))

        s.section("The month has to be one somebody could come in")
        first, last = m.contact_month_window()
        s.check("the window runs from this month",
                first == m.house_today().strftime("%Y-%m"), detail=first)
        s.check("to two years on", last == _month_shift(first, 24), detail=last)
        for who, month, why in (("y1970", "1970-05", "the one that came: 1970"),
                                ("past", _month_shift(first, -1), "last month"),
                                ("far", _month_shift(last, 1), "a month past the window"),
                                ("m13", "%s-13" % first[:4], "a thirteenth month"),
                                ("m00", "%s-00" % first[:4], "a month nought")):
            r, letters = _post(anon, _rough(who, rough_month=month))
            entry, task = _saved(who)
            s.check(f"{why} ({month}): nothing kept, no letter",
                    entry is None and task is None and not letters,
                    detail=str(entry and dict(entry)))
            s.check(f"{why}: thanked the same", flashes(r) == [THANKS], detail=str(flashes(r)))
        for who, month, why in (("now", first, "this month"), ("edge", last, "the furthest one")):
            r, _letters = _post(anon, _rough(who, rough_month=month))
            entry, _task = _saved(who)
            s.check(f"{why} ({month}) is kept, over the whole month",
                    entry is not None and entry["desired_arrival"] == month + "-01",
                    detail=str(entry and (entry["desired_arrival"], entry["desired_departure"])))
        r, _letters = _post(anon, _rough("typed", rough_month="Late September"))
        entry, _task = _saved("typed")
        s.check("a month typed in words, where a browser has no picker, is kept in words",
                entry is not None and entry["desired_arrival"] is None
                and "Around Late September" in (entry["notes"] or ""),
                detail=str(entry and dict(entry)))
        page = anon.get("/contact").get_data(as_text=True)
        s.check("and the picker offers the same window",
                f'min="{first}"' in page and f'max="{last}"' in page)

        s.section("Tell me when that month opens")
        year = m.house_today().year
        notify = {"action": "notify", "notify_month": "November",
                  "notify_year": str(year + 1), "notify_email": _addr("notify")}
        r, _letters = _post(anon, {**notify, **_harness.contact_form_fields()})
        told, _task = _saved("notify")
        s.check("a real one is kept, and kept OPEN: it asked for the letter",
                told is not None and told["status"] == "open",
                detail=str(told and dict(told)))
        notify_reply = (r.status_code, flashes(r))
        r, _letters = _post(anon, {**notify, "notify_email": _addr("notifybot"),
                                   m.BOT_HONEYPOT_FIELD: "x",
                                   m.BOT_STAMP_FIELD: _harness.form_stamp(60)})
        s.check("a machine's is not kept", _saved("notifybot")[0] is None)
        s.check("and is thanked the same", (r.status_code, flashes(r)) == notify_reply,
                detail=str(flashes(r)))
        r, _letters = _post(anon, {**notify, "notify_email": _addr("n1970"), "notify_year": "1970",
                                   **_harness.contact_form_fields()})
        s.check("a year the form does not offer is not kept", _saved("n1970")[0] is None)
        s.check("and is thanked, not refused", r.status_code == 200
                and any("We will write to you once" in f for f in flashes(r)),
                detail=str(flashes(r)))
        r, _letters = _post(anon, {**notify, "notify_email": _addr("n0"), "notify_year": "0",
                                   **_harness.contact_form_fields()})
        s.check("year nought is not an error page", r.status_code == 200,
                detail=f"HTTP {r.status_code} -- date(0, 11, 1) used to raise")
        if m.house_today().month > 1:
            r, _letters = _post(anon, {**notify, "notify_email": _addr("ngone"),
                                       "notify_month": "January", "notify_year": str(year),
                                       **_harness.contact_form_fields()})
            s.check("a month already gone is said so, because a person can pick it",
                    _saved("ngone")[0] is None
                    and any("already gone" in f for f in flashes(r)), detail=str(flashes(r)))

        s.section("An enquiry is never offered a room by letter")
        # Twenty months out, where nobody else in a copied database is waiting.
        arrival = m.parse_date(_month_shift(first, 20) + "-10")
        r, _letters = _post(anon, _rough("offer", rough_month=_month_shift(first, 20)))
        enquiry, _task = _saved("offer")
        conn = db()
        # Somebody genuinely waiting for those nights, who joined AFTER the
        # enquiry -- so the enquiry is first in the queue if it is in it.
        conn.execute(
            """INSERT INTO waitlist_entries (name, email, phone, desired_arrival,
                 desired_departure, party_size, status, created_at)
               VALUES (?, ?, '', ?, ?, 2, 'open', ?)""",
            (f"Waiting {TAG}", _addr("waiting"), arrival.isoformat(),
             (arrival + timedelta(days=3)).isoformat(),
             m.datetime.now(m.timezone.utc).isoformat()))
        was = conn.execute("SELECT value FROM app_settings WHERE key = "
                           "'automation_waitlist_autonotify_enabled'").fetchone()
        conn.execute("""INSERT INTO app_settings (key, value)
                        VALUES ('automation_waitlist_autonotify_enabled', '1')
                        ON CONFLICT(key) DO UPDATE SET value = '1'""")
        conn.commit()
        written = []
        real_email, real_sms = m.send_email, m.send_sms
        m.send_email = lambda to, subj, body, *a, **k: written.append(to) or True
        m.send_sms = lambda conn, to, body, **k: (False, "none")
        try:
            with m.app.test_request_context("/"):
                matched = m.matching_waitlist_entries(
                    conn, arrival.isoformat(), (arrival + timedelta(days=3)).isoformat())
                told = m.notify_room_waitlist_opening(
                    conn, arrival.isoformat(), (arrival + timedelta(days=3)).isoformat())
        finally:
            m.send_email, m.send_sms = real_email, real_sms
            if was:
                conn.execute("UPDATE app_settings SET value = ? WHERE key = "
                             "'automation_waitlist_autonotify_enabled'", (was["value"],))
            else:
                conn.execute("DELETE FROM app_settings WHERE key = "
                             "'automation_waitlist_autonotify_enabled'")
            conn.commit()
        conn.close()
        s.check("the owner is still reminded of it when a night in its month frees up",
                enquiry is not None and enquiry["id"] in [e["id"] for e in matched],
                detail="the waitlist page promises that reminder")
        s.check("but the offer goes to the person waiting for nights",
                written == [_addr("waiting")], detail=str(written))
        s.check("and never to the enquiry", _addr("offer") not in written)
        s.check("which is still an enquiry afterwards",
                _saved("offer")[0]["status"] == "enquiry",
                detail=str(_saved("offer")[0]["status"]))

        s.section("On the waitlist page")
        page = oc.get("/admin/waitlist").get_data(as_text=True)
        card = page[page.find(f'id="entry-{enquiry["id"]}"'):]
        card = card[:card.find('class="expense-card"') if 'class="expense-card"' in card else None]
        s.check("an enquiry shows as Enquiry, chosen in its menu",
                re.search(r'<option value="enquiry"\s+selected', card) is not None)
        s.check("and Open is not offered for it", '<option value="open"' not in card)
        oc.post(f"/admin/waitlist/{enquiry['id']}/status", data={"status": "contacted"})
        oc.post(f"/admin/waitlist/{enquiry['id']}/status", data={"status": "open"})
        s.check("put back from Contacted, even as 'open', it is an enquiry again",
                _saved("offer")[0]["status"] == "enquiry",
                detail=str(_saved("offer")[0]["status"]))
        s.check("and its task is open again", _saved("offer")[1]["status"] == "open")
        conn = db()
        waiting_id = conn.execute("SELECT id FROM waitlist_entries WHERE email = ?",
                                  (_addr("waiting"),)).fetchone()[0]
        conn.close()
        oc.post(f"/admin/waitlist/{waiting_id}/status", data={"status": "closed"})
        oc.post(f"/admin/waitlist/{waiting_id}/status", data={"status": "open"})
        s.check("an entry not from the Contact page goes back to Open",
                _saved("waiting")[0]["status"] == "open")
        s.check("the Enquiry chip finds them",
                f'id="entry-{enquiry["id"]}"' in
                oc.get("/admin/waitlist?state=Enquiry").get_data(as_text=True))

        s.section("Enquiries saved as open before this")
        conn = db()
        old = conn.execute(
            """INSERT INTO waitlist_entries (name, email, phone, party_size, notes, status,
                 created_at, source) VALUES (?, ?, '', 2, 'May, a week', 'open', ?, 'contact')""",
            (f"Old {TAG}", _addr("old"), m.datetime.now(m.timezone.utc).isoformat())).lastrowid
        conn.commit()
        m.tidy_contact_enquiries(conn)
        s.check("are moved to Enquiry at boot",
                conn.execute("SELECT status FROM waitlist_entries WHERE id = ?",
                             (old,)).fetchone()[0] == "enquiry")
        s.check("and one somebody is waiting on is not touched",
                conn.execute("SELECT status FROM waitlist_entries WHERE id = ?",
                             (waiting_id,)).fetchone()[0] == "open")
        conn.close()

        s.section("An old database learns the word")
        fd, path = tempfile.mkstemp(prefix="gudanes_widen_", suffix=".db")
        os.close(fd)
        try:
            old_db = sqlite3.connect(path)
            old_db.row_factory = sqlite3.Row
            old_db.executescript("""
                CREATE TABLE waitlist_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    phone TEXT,
                    desired_arrival TEXT,
                    desired_departure TEXT,
                    party_size INTEGER,
                    notes TEXT,
                    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','contacted','booked','closed')),
                    created_at TEXT NOT NULL
                );
                ALTER TABLE waitlist_entries ADD COLUMN offered_at TEXT;
                ALTER TABLE waitlist_entries ADD COLUMN source TEXT;
                ALTER TABLE waitlist_entries ADD COLUMN spam_at TEXT;
                CREATE INDEX idx_waitlist_entries_status ON waitlist_entries(status);
                INSERT INTO waitlist_entries (name, email, status, created_at, source, offered_at)
                  VALUES ('A', 'a@x', 'open', 't1', 'contact', NULL),
                         ('B', 'b@x', 'contacted', 't2', NULL, 'o2'),
                         ('C', 'c@x', 'closed', 't3', NULL, NULL);
                DELETE FROM waitlist_entries WHERE name = 'C';
            """)
            old_db.commit()
            before = [tuple(r) for r in old_db.execute("SELECT * FROM waitlist_entries ORDER BY id")]
            s.check("it is rebuilt", m.widen_waitlist_statuses(old_db) is True)
            sql = old_db.execute("SELECT sql FROM sqlite_master WHERE name = 'waitlist_entries'").fetchone()[0]
            s.check("its CHECK takes 'enquiry'", "'enquiry'" in sql, detail=sql[-160:])
            s.check("every row and every column kept",
                    [tuple(r) for r in old_db.execute("SELECT * FROM waitlist_entries ORDER BY id")] == before)
            s.check("the status index is back",
                    old_db.execute("SELECT 1 FROM sqlite_master WHERE type = 'index' "
                                   "AND name = 'idx_waitlist_entries_status'").fetchone() is not None)
            new_id = old_db.execute(
                "INSERT INTO waitlist_entries (name, email, status, created_at) "
                "VALUES ('D', 'd@x', 'enquiry', 't4')").lastrowid
            s.check("an enquiry can be saved", new_id is not None)
            s.check("with a NEW id, not the one a deleted entry had",
                    new_id == 4, detail=f"id {new_id}: a task tied to entry 3 would open it")
            try:
                old_db.execute("INSERT INTO waitlist_entries (name, email, status, created_at) "
                               "VALUES ('E', 'e@x', 'whatever', 't5')")
                still_checked = False
            except sqlite3.IntegrityError:
                still_checked = True
            s.check("and the CHECK still refuses a word it does not know", still_checked)
            s.check("a second boot changes nothing", m.widen_waitlist_statuses(old_db) is False)
            old_db.close()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

        s.section("Turnstile")
        page = anon.get("/contact").get_data(as_text=True)
        s.check("without keys it draws nothing", "cf-turnstile" not in page
                and "challenges.cloudflare.com" not in page)
        r, _letters = _post(anon, _rough("nokeys"))
        s.check("and the form works", _saved("nokeys")[0] is not None,
                detail=str(flashes(r)))
        s.check("a site key without its secret is still off",
                (setattr(m, "TURNSTILE_SITEKEY", "0xSITE") or not m.turnstile_enabled()))
        m.TURNSTILE_SECRET = "0xSECRET"
        try:
            page = anon.get("/contact").get_data(as_text=True)
            s.check("with both keys it draws the check",
                    'class="cf-turnstile" data-sitekey="0xSITE"' in page
                    and "challenges.cloudflare.com/turnstile/v0/api.js" in page)
            for who, verdict, kept, what in (
                    ("tsno", False, False, "Cloudflare says no: not kept"),
                    ("tsyes", True, True, "Cloudflare says yes: kept"),
                    ("tsdown", None, True, "Cloudflare cannot be asked: kept, not lost")):
                asked = []
                m.turnstile_siteverify = (lambda v: lambda token, ip: asked.append(token) or v)(verdict)
                r, _letters = _post(anon, _rough(who, **{"cf-turnstile-response": "tok-" + who}))
                s.check(what, (_saved(who)[0] is not None) == kept)
                s.check(f"{what} -- and the token was the one posted", asked == ["tok-" + who],
                        detail=str(asked))
                s.check(f"{what} -- thanked either way", flashes(r) == [THANKS])
        finally:
            m.TURNSTILE_SITEKEY = m.TURNSTILE_SECRET = ""
            m.turnstile_siteverify = blocked_verify

        s.section("Every form that posts to /contact carries the guard")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("_flexible.html", "_notify.html"):
            with open(os.path.join(root, "templates", name), encoding="utf-8") as f:
                src = f.read()
                s.check(f"{name} calls bot_guard() and turnstile_check()",
                        "{{ bot_guard() }}" in src and "turnstile_check()" in src,
                        detail="without it every post is a machine's, and every "
                               "enquiry is thanked and thrown away")
        bare = []
        for path in ("/contact", "/book"):
            html = anon.get(path).get_data(as_text=True)
            for mt in re.finditer(r'<form[^>]*action="/contact"[^>]*>', html):
                chunk = html[mt.start():html.find("</form>", mt.start())]
                if f'name="{m.BOT_STAMP_FIELD}"' not in chunk or f'name="{m.BOT_HONEYPOT_FIELD}"' not in chunk:
                    bare.append(path)
        s.check("and every one drawn on a page has both fields", not bare, detail=str(bare))
        s.check("the honeypot is out of reach: off the tab order",
                'tabindex="-1"' in str(m.bot_guard_fields()))
    finally:
        m.TURNSTILE_SITEKEY = m.TURNSTILE_SECRET = ""
        m.turnstile_siteverify = blocked_verify
        m.PUBLIC_FORM_MIN_SECONDS = min_was
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
