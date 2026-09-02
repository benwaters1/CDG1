"""What the stay actually costs, on the page where somebody decides.

A guest saw a nightly rate, did the arithmetic in their head, and met the
deposit split for the first time at the payment step. This puts the whole
figure on the room page as they change the nights.

THE REASON THIS FILE IS LONGER THAN THE FEATURE is the deposit. The sketch
had a hardcoded 30% fallback, and this house's room deposit defaults to ZERO
— so on the house as configured it would have told every guest they must pay
30% now. A wrong figure, about their own money, on the page where they
decide, is worse than not shipping the feature at all.

And the right figure is not simply "the setting". resolve_deposit_percent can
give a different answer per date and per party size, and the calculator runs
before any dates are picked. So there are three cases and each has to say
something true:

  - one figure applies to everyone → show it;
  - the house takes nothing → say nothing is taken, rather than "0";
  - a rule scopes it to dates or party size → say it depends, rather than
    quoting one and being wrong for somebody at checkout.

The last is the one a naive implementation gets wrong, because it looks
right on the page nine times out of ten.
"""
from _harness import Suite, db

import _harness

m = _harness.m
TAG = "ZZCOST"


def _cleanup(conn):
    conn.execute("DELETE FROM deposit_rules WHERE category = 'room' "
                 "AND COALESCE(label, '') LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("what the stay costs")
    conn = db()
    now = m.datetime.now(m.timezone.utc).isoformat()
    _cleanup(conn)

    conn.execute(
        """INSERT INTO rooms (name, description, max_occupancy, max_adults,
                   price_per_night, min_nights, active, sort_order, export_token)
           VALUES (?, '', 2, 2, 250, 3, 1, 97, ?)""",
        (TAG + " Tower Room", "tok-" + TAG.lower()))
    room_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()

    anon = m.app.test_client()

    def page():
        r = anon.get(f"/book/{room_id}")
        return r.status_code, r.get_data(as_text=True)

    s.section("The arithmetic is done for them")
    code, body = page()
    s.check("the room page opens", code == 200, detail=f"HTTP {code}")
    s.check("the calculator is on it", "g-cost" in body)
    s.check("it starts at the minimum stay", 'value="3"' in body,
            detail="a three-night minimum starting at one night shows a total "
                   "the guest cannot actually book")
    s.check("and the total is the nights times the rate",
            "€750" in body, detail="250 a night, three nights")

    s.section("A house that takes no deposit says so")
    # The default. The sketch would have said "a 30% deposit holds the room"
    # here, which is a wrong figure about the guest's money on the page where
    # they decide.
    setting = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'room_deposit_percent'"
    ).fetchone()
    pct = float(setting["value"]) if setting else 0.0
    if pct:
        s.check("this house takes a deposit, so the zero case is untested here",
                False,
                detail=f"room_deposit_percent is {pct}. Reported rather than "
                       "skipped: the branch below is the one the sketch got "
                       "wrong, and it wants exercising.")
    else:
        s.check("it says nothing is taken to hold it",
                "Nothing is taken to hold it" in body)
        s.check("and does not offer a 0 to hold", "To hold it now" not in body,
                detail="'to hold it now: 0' invites the question of what that "
                       "means, and the answer is nothing")
        s.check("nor any invented percentage", "30%" not in body,
                detail="the sketch's hardcoded fallback")

    s.section("A house with one flat deposit shows the figure")
    conn.execute(
        """INSERT INTO deposit_rules (category, deposit_percent, label,
                   created_at)
           VALUES ('room', 40, ?, ?)""", (TAG + " flat", now))
    conn.commit()
    s.check("the blanket rule is what is shown",
            m.deposit_percent_to_show(conn) == 40,
            detail=str(m.deposit_percent_to_show(conn)))
    code, body = page()
    s.check("the page quotes it", "A 40% deposit holds the room" in body)
    s.check("and splits the total", "To hold it now" in body)
    s.check("into the right halves", "€300" in body and "€450" in body,
            detail="40% of 750 is 300, and 450 later")

    s.section("A deposit that DEPENDS on the booking is not quoted")
    # The one a naive version gets wrong, because it looks right nine times
    # out of ten. A rule scoped to August means a guest sees 40% on the room
    # page and 60% at checkout.
    conn.execute(
        """INSERT INTO deposit_rules (category, deposit_percent, start_date,
                   end_date, label, created_at)
           VALUES ('room', 60, '2027-08-01', '2027-08-31', ?, ?)""",
        (TAG + " august", now))
    conn.commit()
    s.check("nothing is offered as THE figure",
            m.deposit_percent_to_show(conn) is None,
            detail=str(m.deposit_percent_to_show(conn)))
    code, body = page()
    s.check("the page says how much depends on the dates",
            "how much depends on the dates" in body)
    s.check("and quotes no percentage at all",
            "40% deposit" not in body and "60% deposit" not in body,
            detail="a figure on the page that changes at checkout is the "
                   "thing this whole branch exists to prevent")
    s.check("nor a split of the total", "To hold it now" not in body)

    s.section("A rule scoped to party size counts as depending too")
    conn.execute("DELETE FROM deposit_rules WHERE label = ?", (TAG + " august",))
    conn.execute(
        """INSERT INTO deposit_rules (category, deposit_percent, min_party_size,
                   label, created_at)
           VALUES ('room', 60, 6, ?, ?)""", (TAG + " big party", now))
    conn.commit()
    s.check("a party-size rule also makes it depend",
            m.deposit_percent_to_show(conn) is None,
            detail="a group of eight paying more than a couple is exactly "
                   "what one flat figure cannot express")

    s.section("It works more than once on a page")
    # querySelector picks the FIRST match. A page listing three rooms would
    # have had two calculators whose numbers never move, which reads as a
    # broken price rather than a broken widget.
    import io as _io
    import os as _os
    src = _io.open(_os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "templates", "_nights_calc.html"),
        encoding="utf-8").read()
    s.check("the script binds every calculator, not the first",
            "querySelectorAll('[data-cost]')" in src,
            detail="querySelector picks one")
    s.check("and the input id is per room, not fixed",
            'id="cost_n_{{ uid or room[\'id\'] }}"' in src,
            detail="two elements with the same id is a label pointing at "
                   "whichever the browser saw first")

    s.section("And the arithmetic degrades to something true with no script")
    # The figures are rendered server-side as well, so a guest with no
    # JavaScript sees the minimum-stay total rather than an empty box.
    s.check("the total is in the HTML, not only in the script",
            ">€750</dd>" in body or "€750" in body.split("<script")[0],
            detail="a price that only exists once a script runs is a price "
                   "some guests never see")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
