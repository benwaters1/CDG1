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

from datetime import date, timedelta
import io as _io
import os as _os
import re as _re

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

    s.section("What a stay costs, before any script has run")
    # The second calculator went on 5 October at the owner's request: two
    # things on one page were counting nights differently. The form's own
    # summary card is the only price now. What has to survive that is not a
    # widget but an answer -- a guest reading with nothing executing must
    # still learn the rate and the minimum, because the total is built from
    # them and they are the part the server knows.
    code, body = page()
    s.check("the room page opens", code == 200, detail=f"HTTP {code}")
    # The PRICE AS WRITTEN, not the bare digits. A control that blanked the
    # visible figure walked straight through "250 in body": the number
    # survives in a data-eur attribute that feeds the quote script and that
    # no guest can read. Asking for the formatted price is asking whether it
    # was rendered for a person.
    s.check("the nightly rate is in the HTML, not only in a script",
            "€250" in body and "per night" in body,
            detail="a price that exists only once a script runs is a price "
                   "some guests never see")
    s.check("and the minimum stay is stated",
            "3-night minimum" in body,
            detail="without it a guest prices a stay the house will not take")

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
        # ZERO DOES NOT MEAN NOTHING IS TAKEN. A room is paid for in full
        # at the moment it is booked -- room_payment_schedule returns
        # (total, 0, None) for it: the whole stay now, nothing later, no
        # balance date. The page used to say "nothing is taken to hold it;
        # the stay is settled before you arrive", which told a guest the
        # money came later and then charged them the lot at checkout.
        s.check("it says the stay is paid in full at booking",
                "paid in full when you book" in body,
                detail="a room takes no DEPOSIT because it takes the whole "
                           "amount, which is not the same as taking nothing")
        s.check("and does not promise the money comes later",
                "settled before you arrive" not in body
                and "nearer the date" not in body,
                detail="the one thing a guest must not read on a page that "
                       "is about to charge them the whole stay")
        # The page and the charge, from the same source. This is the check
        # that would have caught it: the wording was hand-written for the
        # branch rather than derived from what the schedule actually does.
        conn2 = db()
        dep, bal, due = m.room_payment_schedule(
            conn2, date.today() + timedelta(days=60), 1200.0, 2)
        conn2.close()
        s.check("and the schedule agrees: everything now, nothing later",
                dep == 1200.0 and bal == 0.0 and due is None,
                detail=f"take now {dep}, later {bal}, due {due} — if this "
                       "ever changes the sentence above has to change with it")
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
    # The wording lived in the calculator the owner had removed, so the split
    # is checked where it is computed rather than where it used to be shown.
    dep_c, bal_c, due_c = m.room_payment_schedule(
        conn, date.today() + timedelta(days=60), 750.0, 2)
    s.check("the schedule splits a 40% rule into the right halves",
            abs(dep_c - 300.0) < 0.01 and abs(bal_c - 450.0) < 0.01,
            detail=f"take now {dep_c}, later {bal_c} — 40% of 750 is 300")
    # AND THE GAP, NAMED. Nothing on the room page says any of that now. The
    # house takes the whole amount today so nothing is wrong on the live
    # site, but a rule set tomorrow would be invisible to the guest deciding.
    # Written as a check so that the day it is put back this fails and has to
    # be deleted on purpose, rather than sitting in a comment nobody reads.
    s.check("and the page does not disclose it, which is a gap not a pass",
            "A 40% deposit holds the room" not in body
            and "To hold it now" not in body,
            detail="if this fails the disclosure has returned to the page: "
                   "delete this check and restore the two above it")

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
    # Same gap, other branch: a deposit that varies was explained in the same
    # removed markup. What must still hold is that NO figure is offered, which
    # is the half that could mislead, and that is checked below.
    s.check("no figure is put in front of the guest for a varying deposit",
            "how much depends on the dates" not in body,
            detail="recorded alongside the gap above; the sentence went with "
                   "the calculator")
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

    s.section("And the figures survive with no script at all")
    # MEASURED, not approximated. This used to ask whether the total appeared
    # before the first <script> tag, which this page defeats by carrying an
    # inline script near the top -- everything real on the page sits after it.
    # Stripping the script blocks and reading what is left is the actual
    # question: would a guest with nothing executing still learn the price?
    import re as _re2
    markup = _re2.sub(r"(?is)<script.*?</script>", "", body)
    s.check("the nightly rate is in the markup",
            "€250" in markup and "per night" in markup,
            detail="a price that exists only once a script runs is a price "
                   "some guests never see")
    s.check("so is the minimum stay", "3-night minimum" in markup,
            detail="the rate alone prices a stay the house will not take")
    s.check("and so is what they will actually be charged",
            "paid in full when you book" in markup,
            detail="the whole amount leaves the card at booking; a guest who "
                   "cannot read that before pressing the button is told "
                   "nothing by a page that knows")

    _cleanup(conn)
    conn.close()
    s.section("The room deposit never comes from the restaurant's settings")
    # `settings` on a room page is the RESTAURANT settings row, and it has a
    # deposit_percent column of its own. A template reading it there is not
    # reading a missing key -- it is reading the wrong half of the business.
    # It is None today, which is why the fallback fired instead; set a dinner
    # deposit and the room page would quietly start quoting it at people
    # booking a bed. Twice now a new component has done this.
    import glob as _g
    import os as _o
    import re as _re
    # _nights_calc.html was on this list and is deleted: the owner had the
    # second calculator removed on 5 October and the file was left behind.
    ROOM_PAGES = ("book_room.html", "book_rooms.html", "_before.html",
                  "_paydates.html", "_weekcost.html",
                  "_stay_panel.html", "home.html")
    wrong = []
    for path in _g.glob(_o.path.join(_harness.ROOT, "templates", "*.html")):
        name = _o.path.basename(path)
        if name not in ROOM_PAGES:
            continue
        body = open(path, encoding="utf-8").read()
        # Only inside a Jinja expression. The comment explaining why this is
        # wrong names the string, and a check that fails on its own
        # explanation is one somebody deletes.
        for expr in _re.findall(r"\{[{%](.*?)[}%]\}", body, _re.S):
            if "settings['deposit_percent']" in expr:
                wrong.append(name)
                break
    s.check("no room page reads a deposit out of the restaurant settings",
            not wrong,
            detail=", ".join(wrong) + " — the room figure comes from the route, "
                   "through deposit_percent_to_show")

    s.section("And no template invents a deposit percentage of its own")
    # Asked of the SOURCE, not of a rendered page, because this keeps coming
    # back somewhere a page-level check cannot reach. The fifth occurrence
    # arrived inside a brand new macro, and the sixth was JavaScript --
    # Number(... else 30) working out a euro figure in the browser, which no
    # check on rendered text would ever have seen.
    #
    # deposit_percent_to_show() is the one answer and it has three: a figure,
    # None for "it depends on the dates and the party", or nothing at all. A
    # template picking its own default quotes a guest a number about their
    # own money that the checkout will disagree with.
    tpl_dir = _os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "templates")
    invented = []
    for root, _dirs, files in _os.walk(tpl_dir):
        for fn in sorted(files):
            if not fn.endswith(".html"):
                continue
            body = _re.sub(r"\{#.*?#\}", "",
                           _io.open(_os.path.join(root, fn),
                                    encoding="utf-8").read(), flags=_re.S)
            for i, line in enumerate(body.splitlines(), 1):
                # A form's default for a NEW record is the owner setting a
                # figure, not a page inventing one to show a guest.
                if "<input" in line:
                    continue
                if _re.search(r"deposit_percent[^\n]{0,120}?\belse\s+(?!0\b)\d+",
                              line):
                    invented.append("%s:%d" % (fn, i))
    s.check("no template falls back to a percentage of its own",
            not invented,
            detail="; ".join(invented) + " -- deposit_percent_to_show() is "
                   "the one answer, and 'it depends' is one of its three")
    s.check("there were templates to read at all",
            any(f.endswith(".html") for _r, _d, fs in _os.walk(tpl_dir)
                for f in fs),
            detail="a sweep with nothing to sweep passes for free")

    return s


if __name__ == "__main__":
    print(run().report())
