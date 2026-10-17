"""The winter rest is the room's own closure, not the template's guess.

WHAT WAS THERE. The two suites rest from 1 December to mid-February ("too
large to heat"), and z26 gave Stay a winter box for them: "Une suite pour la
belle saison", the reopening line, and the other rooms saying how they are
kept warm. book_rooms.html chose WHICH rooms by name ('The Marquis Suite',
'The Family Suite') and WHEN by comparing month strings ('12-01', '02-15').
So a renamed suite lost its box, a room rested in March got "Not available
these dates", and a suite simply booked in December was dressed as resting.
The closures were already data: the dates are room blocks the booking check
refuses, and one closed "for the season" carries the owner's line for guests.

WHAT THIS PINS (z27 item 17).
  - The box follows a closure for the season, on any room and in any month,
    with the owner's line inside it and z26's wording kept.
  - The other rooms say how they are kept warm only while a room is resting.
  - A suite unavailable for any other reason says "Not available these
    dates", and the house's private reason for a closure is never printed.
  - The room page's calendar reads the owner's line too.
  - The template names no room and compares no month, so a handover that
    puts the old test back is named here.
"""
import os
import re
from datetime import date, timedelta

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAG = "ZZREST"
NOTE = "ZZREST The room is resting until the 20th."
WINTER_WORDS = "The suites are open from mid-February to the end of November."


def guesswork(text):
    """Room names and month-day strings inside the template's logic.

    Comments go first ({# #} is a note about the page, and the history of
    this box lives in one). What is left inside {% %} and {{ }} is what the
    page decides with.
    """
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
    found = []
    for tag in re.findall(r"\{%.*?%\}|\{\{.*?\}\}", text, flags=re.S):
        if re.search(r"(Marquis|Family|Gold) Suite", tag) and "_winter" in tag:
            found.append(tag.strip()[:120])
        if "_wdates" in tag and re.search(r"'\d\d-\d\d'", tag):
            found.append(tag.strip()[:120])
    return found


def _card(page, room_id):
    """One room's card on the Stay page, or ''."""
    start = page.find(f'id="room-{room_id}"')
    if start < 0:
        return ""
    end = page.find("</article>", start)
    return page[start:end if end > 0 else None]


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM room_blocks WHERE reason LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _close(room_id, start, end, mode, note, reason):
    conn = db()
    try:
        done, skipped = m.close_rooms(conn, [room_id], start, end, mode, note, reason, None)
        conn.commit()
    finally:
        conn.close()
    return done, skipped


def _search(client, a, b):
    return client.get(f"/book?arrival={a.isoformat()}&departure={b.isoformat()}"
                      ).get_data(as_text=True)


def run():
    s = Suite("winter_rest")
    _cleanup()
    try:
        _checks(s)
    finally:
        _cleanup()
    return s


def _checks(s):
    clients()
    anon = m.app.test_client()
    conn = db()
    rooms = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, id").fetchall()
    conn.close()
    s.check("there are rooms to rest and rooms to leave open", len(rooms) >= 3, detail=str(len(rooms)))
    if len(rooms) < 3:
        return
    # Not a suite, and not in winter: neither the names nor the months can be
    # what decides it.
    plain = next((r for r in rooms if "suite" not in r["name"].lower()), rooms[0])
    others = [r for r in rooms if r["id"] != plain["id"]]
    year = house_today().year + 3
    a, b = date(year, 3, 10), date(year, 3, 13)

    s.section("A room closed for the season in March, on a room that is not a suite")
    done, skipped = _close(plain["id"], a - timedelta(days=3), a + timedelta(days=10),
                           "season", NOTE, TAG + " heating works")
    s.check("the closure is made", done == [plain["name"]], detail=str(skipped))
    page = _search(anon, a, b)
    card = _card(page, plain["id"])
    s.check("its card is on the page", bool(card))
    s.check("and draws the winter box, not the grey button",
            'class="g-winter"' in card and "Kept for the warmer months" in card
            and "Not available these dates" not in card, detail=card[-600:])
    s.check("with the owner's line inside it, first", NOTE in card
            and card.find(NOTE) < card.find(WINTER_WORDS), detail=card[-600:])
    s.check("and z26's winter wording kept", WINTER_WORDS in card)
    s.check("never the house's own reason", "heating works" not in page)
    open_cards = [_card(page, r["id"]) for r in others]
    s.check("the rooms left open say how they are kept warm",
            all("Space heater and electric blankets" in c for c in open_cards if c)
            and any(open_cards), detail=str([bool(c) for c in open_cards]))
    s.check("and draw no winter box of their own",
            not any('class="g-winter"' in c for c in open_cards))

    s.section("The room page reads the same line")
    conn = db()
    try:
        with m.app.test_request_context():
            nights = m.unavailable_nights(conn, plain["id"], a, b)
    finally:
        conn.close()
    s.check("its calendar greys those nights with the owner's line",
            set(nights.values()) == {NOTE.rstrip(".")}, detail=str(nights))
    quote = anon.get(f"/api/quote?room_id={plain['id']}&arrival={a.isoformat()}"
                     f"&departure={b.isoformat()}&party_size=2").get_json() or {}
    s.check("and its price box says it too, rather than 'Not available'",
            quote.get("available") is False and quote.get("reason") == NOTE,
            detail=str(quote.get("reason")))

    s.section("December, with nothing resting: no winter box anywhere")
    d_a, d_b = date(year, 12, 10), date(year, 12, 13)
    suite = next((r for r in rooms if "suite" in r["name"].lower()), others[0])
    # Off sale for a reason of the house's own, shown as booked.
    _close(suite["id"], d_a - timedelta(days=1), d_b + timedelta(days=1),
           "booked", "", TAG + " family visit")
    page = _search(anon, d_a, d_b)
    card = _card(page, suite["id"])
    s.check("a suite off sale in December for another reason says so plainly",
            "Not available these dates" in card and 'class="g-winter"' not in card,
            detail=card[-500:])
    s.check("and nobody is told about heaters", "Space heater and electric blankets" not in page)
    s.check("nor the house's reason", "family visit" not in page)

    s.section("Rest it, and December reads as winter")
    _cleanup()
    _close(suite["id"], d_a - timedelta(days=9), d_b + timedelta(days=40), "season", "", TAG + " winter")
    page = _search(anon, d_a, d_b)
    card = _card(page, suite["id"])
    said = m.default_closure_note(suite["name"], (d_b + timedelta(days=40)).isoformat())
    s.check("the suite's box says when it reopens, in its own name", said in card
            and 'class="g-winter"' in card, detail=card[-500:])

    s.section("The template decides with data, not names or months")
    text = open(os.path.join(ROOT, "templates", "book_rooms.html"), encoding="utf-8").read()
    found = guesswork(text)
    s.check("book_rooms.html names no suite and compares no month for the winter box",
            found == [], detail="; ".join(found[:4]))
    s.check("the scan finds the old test when it is there",
            guesswork("{%- set _winter_room = room_name(room['name']) in ['The Marquis Suite'] %}"
                      "{%- set _wdates = searched and (_am >= '12-01') %}") != []
            and guesswork("{# _winter_room by 'The Marquis Suite', _wdates '12-01' #}") == [])


if __name__ == "__main__":
    print(run().report())
