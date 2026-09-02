"""Two of the unlanded guest macros, actually landed.

test_orphan_templates found nine guest-facing macros written against a
contract and never put on a page. These are the first two, and both were
written against COLUMN NAMES THAT DO NOT EXIST — b['arrival'], b['departure'],
b['balance_due'], b['reference']. On a sqlite3.Row a missing key raises, so
including either of them as written would have been a 500 on every booking
rather than a quiet blank. That is what an unlanded sketch looks like from the
outside, and it is why "it is already written, just include it" is never true.

WHAT HAPPENS NEXT. After booking, a guest knows it is confirmed and nothing
else: not when the balance falls due, not what arrives a week out, not what
time they can come. Those are four emails the house writes by hand every time,
and the balance one is the email guests write FIRST.

TAKE THIS WITH YOU. The valley has patchy signal and the drive is unlit. A
booking that lives only in an email is nothing in the last twenty minutes in
the dark. The print rules are the feature rather than decoration: the button
says "everything else on this page is left off", and without them it prints
the whole nine-section booking page, payment form included — a promise whose
failure the guest only discovers standing at a printer.

The print rules use visibility rather than display for a reason worth keeping:
display:none on an ancestor cannot be undone by a descendant, so hiding the
page that way hides the sheet with it.
"""
from datetime import timedelta

from _harness import Suite, db, house_today

import _harness

m = _harness.m
TAG = "ZZGPAGE"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("the guest's own page")
    today = house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()
    conn = db()
    _cleanup(conn)
    room = conn.execute("SELECT id, name FROM rooms ORDER BY id LIMIT 1").fetchone()

    def add(ref, *, total, paid, due_date=None, status="confirmed"):
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token,
                       guest_name, guest_email, arrival_date, departure_date,
                       party_size, status, total_price, amount_paid,
                       balance_due_date, deposit_amount, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, ?, ?)""",
            (room["id"], TAG + ref, f"tok-{TAG}-{ref}".lower(),
             TAG + " " + ref.title(), f"{TAG}.{ref}@example.invalid".lower(),
             (today + timedelta(days=30)).isoformat(),
             (today + timedelta(days=33)).isoformat(), status, total, paid,
             due_date, round(total * 0.3, 2), now))
        return f"tok-{TAG}-{ref}".lower()

    owing = add("OWING", total=1000, paid=300,
                due_date=(today + timedelta(days=10)).isoformat())
    settled = add("SETTLED", total=1000, paid=1000)
    rounding = add("ROUNDING", total=1000, paid=999.6)
    conn.commit()

    anon = m.app.test_client()

    def page(token):
        r = anon.get(f"/book/manage/{token}")
        return r.status_code, r.get_data(as_text=True)

    s.section("What happens next, on a booking with a balance")
    code, body = page(owing)
    s.check("the page opens", code == 200, detail=f"HTTP {code}")
    s.check("the timeline is on it", "What Happens Next" in body)
    s.check("it says what is still owed", "€700" in body,
            detail="1000 taken, 300 paid")
    s.check("and the day it falls due",
            (today + timedelta(days=10)).isoformat() in body,
            detail="'when do I pay?' is the email guests write first")
    s.check("it says nobody will ask for card details by email",
            "nobody will ask for card details by email" in body,
            detail="the sentence that makes a phishing email obvious")

    s.section("And on one already settled")
    code, body = page(settled)
    s.check("it says there is nothing to settle",
            "nothing further to settle" in body)
    s.check("and does not ask for a balance", "€0" not in body,
            detail="a guest who has paid must not be shown a figure")
    # The words AND the marker. Checking only the words let the balance step
    # go back to reading as outstanding while still saying it was paid --
    # which is the shape of every timeline that quietly stops meaning
    # anything.
    settled_done = body.count("is-done")
    code, owing_body = page(owing)
    s.check("the balance step is marked as done, not just worded as done",
            settled_done > owing_body.count("is-done"),
            detail=f"{settled_done} done on the settled booking, "
                   f"{owing_body.count('is-done')} on the one still owing")

    s.section("A few cents of rounding is paid, not owed")
    # 999.60 against 1000 is a part payment that rounded. "0 still to settle"
    # in front of somebody who has paid is worse than saying nothing.
    code, body = page(rounding)
    s.check("it reads as settled", "nothing further to settle" in body,
            detail="40 cents is not a balance")

    s.section("Take this with you")
    code, body = page(owing)
    s.check("the sheet is on the page", "Take this with you" in body)
    s.check("with the reference", TAG + "OWING" in body)
    s.check("the room", room["name"] in body)
    s.check("the dates", (today + timedelta(days=30)).isoformat() in body)
    s.check("a telephone number", "+33" in body)
    s.check("and the last stretch of the drive",
            "gates are on the left" in body,
            detail="the part a map does not tell you in the dark")

    s.section("And it really does print to one sheet")
    # The button says everything else is left off. Without the body class the
    # rules never fire and it prints the whole booking page, payment form and
    # all -- which the guest discovers standing at a printer.
    s.check("the page carries the class the print rules key on",
            "g-print-stay" in body,
            detail="without it the promise on the button is the visible part "
                   "of a lie")
    import io as _io
    import os as _os
    css = _io.open(_os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "static", "style.css"),
        encoding="utf-8").read()
    s.check("and the rules exist", "body.g-print-stay" in css)
    block = css[css.find("body.g-print-stay"):]
    block = block[:block.find("}\n}") + 3]
    s.check("they hide by visibility, not display",
            "visibility:hidden" in block and "display:none" not in block,
            detail="display:none on an ancestor cannot be undone by a "
                   "descendant, so hiding the page that way hides the sheet "
                   "with it")
    s.check("the print button is left off the printed sheet",
            "no-print" in block)

    s.section("Neither macro reads a column that does not exist")
    # The check that would have caught this before it shipped. Both were
    # written against b['arrival'], b['departure'], b['balance_due'] and
    # b['reference'], and a missing key on a sqlite3.Row RAISES -- so this
    # was a 500 on every booking, not a blank line.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bookings)")}
    import re
    for tpl in ("_guest_timeline.html", "_print_stay.html"):
        src = _io.open(_os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__))), "templates", tpl),
            encoding="utf-8").read()
        # Comments stripped first. Both files now explain the fix by
        # QUOTING the old wrong names, and reading those as live code made
        # this fail on the very thing it was meant to prove was fixed.
        stripped = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
        used = set(re.findall(r"""b\[['"]([a-z_]+)['"]\]""", stripped))
        missing = sorted(used - cols)
        s.check(f"{tpl} only reads real columns", not missing,
                detail="not on bookings: " + ", ".join(missing))

    s.section("A booking that has not been confirmed shows neither")
    conn.execute("UPDATE bookings SET status = 'pending' WHERE reference_code = ?",
                 (TAG + "OWING",))
    conn.commit()
    code, body = page(owing)
    s.check("the page still opens", code == 200, detail=f"HTTP {code}")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
