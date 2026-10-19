"""Confirmed places that look like a script's are put in front of the owner.

Before the deposit hold, a form filled with random letters could keep a
confirmed place with nothing paid and no card. One did: "Cooking in the
Cuisine", June 2027, party of two, every field random letters (18 October
2026). Those registrations are NOT cancelled by the app -- the owner decides.
The owner home lists them as a warning that closes itself: cancelling one, a
deposit arriving, or the owner saying it is a real person takes it off.

The reading of "random letters" is a reason to look, never a decision, so
the expensive mistake is calling a real guest a bot. Real names that are hard
on the eye -- Knightsbridge, Schwarzkopf, Brzęczyszczykiewicz, DeLaCruz --
are checked to stay out, alongside the script-typed ones checked to be in.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZBRL"

REAL = ["Knightsbridge", "Schwarzkopf", "Strzelczyk", "McDonald", "DeLaCruz", "Ffrangcon",
        "Llywelyn", "Nguyen", "Tchaikovsky", "Brzęczyszczykiewicz", "Rhythm", "Ystradgynlais",
        "Szczepański", "Wojciechowski", "Chateaubriand", "Pfeiffer", "Hirschsprung",
        "MacGregor", "Saint-Girons", "Mbappé", "Kristensen", "Schmidt", "Dzhugashvili",
        "SMITH", "Ndlovu", "Wrzosek", "VanDerBerg", "LeBlanc", "DiCaprio"]
SCRIPTED = ["xKqPzTvL", "kjhsdfkjh", "aksjdhfg", "qwrtzpkl", "AbCdEfGh", "RkTzYbWq",
            "hgfdsklj", "JbXoVqUtY"]


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
    for table in ("workshop_transactions", "workshop_booking_guests", "workshop_messages"):
        conn.execute(f"DELETE FROM {table} WHERE workshop_booking_id IN {ids}", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _session(label, starts_in):
    conn = db()
    now = _harness.datetime_now()
    title = f"{TAG} {label}"
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at, deposit_percent)
           VALUES (?, '', 1200, 10, 1, 96, ?, 30)""", (title, now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (title,)).fetchone()["id"]
    start = house_today() + timedelta(days=starts_in)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes,
           created_at) VALUES (?, ?, ?, 10, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=5)).isoformat(), f"{TAG} {label}", now))
    conn.commit()
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (f"{TAG} {label}",)).fetchone()["id"]
    conn.close()
    return sid


def _reg(ref, sid, name, email, address, city, *, card=False, deposit_paid=False):
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size,
           status, reference_code, manage_token, created_at, total_price, deposit_amount,
           guest_address, guest_city, stripe_customer_id, stripe_payment_method_id,
           deposit_paid_at)
           VALUES (?, ?, ?, 2, 'confirmed', ?, ?, ?, 2400, 720, ?, ?, ?, ?, ?)""",
        (sid, name, email, f"{TAG}{ref}", f"tok{TAG}{ref}", now, address, city,
         "cus_x" if card else None, "pm_x" if card else None, now if deposit_paid else None))
    conn.commit()
    bid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    conn.close()
    return bid


def _listed():
    conn = db()
    ids = [r["id"] for r in m.suspect_unpaid_registrations(conn, house_today())
           if r["reference_code"].startswith(TAG)]
    conn.close()
    return ids


def _warning():
    conn = db()
    with m.app.test_request_context():
        warns = m.owner_home_warnings(conn, house_today())
    conn.close()
    return [w for w in warns if "look like a bot" in w["title"] or "looks like a bot" in w["title"]]


def run():
    s = Suite("Confirmed places that look like a script's, listed for the owner")
    oc, _ec, _o, _e = clients()
    _cleanup()

    s.section("What reads as typed by a script")
    wrong = [w for w in REAL if m.word_looks_random(w)]
    s.check("no real name from this list is taken for random letters", not wrong, detail=str(wrong))
    missed = [w for w in SCRIPTED if not m.word_looks_random(w)]
    s.check("every script-typed word is", not missed, detail=str(missed))
    s.check("one odd word in a real address is not enough",
            not m.text_looks_random("12 Knightsbridge Gardens, Flat Qxz"))

    s.section("With nothing like that on the books, there is nothing to say")
    s.check("the warning panel says nothing about bots", not _warning(),
            detail=str([w["title"] for w in _warning()]))

    sid = _session("Cooking", 250)
    past = _session("Gone", -30)
    bot = _reg("BOT", sid, "XkqJzTp WvLmRq", "kjhgfdsazx@mail.example", "QwRtYpLk 12", "ZxCvBnMq")
    real = _reg("REAL", sid, "Hans Schwarzkopf", "hans.schwarzkopf@example.invalid",
                "Hauptstrasse 5", "Knightsbridge")
    carded = _reg("CARD", sid, "PqXzLmKj RtWvBn", "zxqwrtp@mail.example", "BcDfGhJk 3",
                  "MnPqRsTv", card=True)
    paid = _reg("PAID", sid, "LkJhGfDs QwErTy", "hjkgfdsq@mail.example", "TzRwQp 9",
                "VbNmLkJh", deposit_paid=True)
    over = _reg("OVER", past, "WqZxCvBn PlMkOj", "qwzxcvb@mail.example", "ZzXxCc 1", "QqWwEe")

    s.section("The one that looks like a bot is listed, and nothing else")
    listed = _listed()
    s.check("the random-letters registration is listed", bot in listed, detail=str(listed))
    s.check("a real guest with hard names is not", real not in listed)
    s.check("one with a card kept is not", carded not in listed)
    s.check("one whose deposit is paid is not", paid not in listed)
    s.check("one on a session already over is not", over not in listed)
    w = _warning()
    s.check("the owner home says so", len(w) == 1 and w[0]["count"] == 1,
            detail=str([(x["title"], x["count"]) for x in w]))
    s.check("and links to that registration", w and f"q={TAG}BOT" in w[0]["href"],
            detail=w[0]["href"] if w else "")
    page = oc.get(f"/admin/workshops/registrations?q={TAG}BOT&when=all").get_data(as_text=True)
    s.check("the register flags it, with a way to say it is a real person",
            "looks like a bot" in page and "This is a real person" in page)
    conn = db()
    status = conn.execute("SELECT status FROM workshop_bookings WHERE id = ?", (bot,)).fetchone()["status"]
    conn.close()
    s.check("and the app has not cancelled it", status == "confirmed")

    s.section("It closes itself")
    r = oc.post(f"/admin/workshops/registrations/{bot}/not-a-bot", follow_redirects=True)
    s.check("'this is a real person' takes it off", bot not in _listed() and not _warning(),
            detail=str(r.status_code))
    conn = db()
    conn.execute("UPDATE workshop_bookings SET bot_check_cleared_at = NULL WHERE id = ?", (bot,))
    conn.commit()
    conn.close()
    s.check("it is back while nobody has said so", bot in _listed())
    conn = db()
    conn.execute("UPDATE workshop_bookings SET status = 'cancelled' WHERE id = ?", (bot,))
    conn.commit()
    conn.close()
    s.check("and cancelling it takes it off too", bot not in _listed() and not _warning())
    s.check("the button refuses a registration that does not exist",
            oc.post("/admin/workshops/registrations/99999999/not-a-bot").status_code == 404)

    _cleanup()
    return s
