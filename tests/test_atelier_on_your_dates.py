"""An atelier on the dates a guest asked for is offered, not only given as the reason.

The owner, 23 November 2026: "when someone enters dates but theres a wrkshop on
i want to point them there" -- "do a proper job of it though". An atelier has
the whole château, so on its nights every room says no; until now /book said
"No rooms free on these dates -- a workshop holds the whole château", and the
room page "Those dates are already reserved. Please choose others." Both true,
and both sent the guest away on the nights the house was offering more.

What is checked: the card names the atelier, its dates, what it includes (the
sentence its own letters use), its price and its places, with the way to take
one and the stays of the guest's own length either side where a room can be
had -- on /book, on a room's page, and in /api/quote, which the room page draws
from as its dates change. It appears exactly on the nights the atelier holds
(not the morning its guests leave), never for a private stay or a sitting
called off, offers no place on one that is full or under way, and every link
in it goes where it says.
"""
import html as htmllib
import re
from datetime import timedelta
from urllib.parse import urlsplit, parse_qs

from _harness import Suite, db, house_today
import _harness

m = _harness.m
TAG = "ZZON"


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(
        "SELECT workshop_sessions.id FROM workshop_sessions JOIN workshops "
        "ON workshops.id = workshop_sessions.workshop_id WHERE workshops.title LIKE ?",
        (TAG + "%",))]
    if ids:
        marks = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM workshop_bookings WHERE session_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM workshop_sessions WHERE id IN ({marks})", ids)
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()


def _text(markup):
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def _card(html):
    found = re.search(r'<aside class="g-onyour".*?</aside>', html, re.S)
    return found.group(0) if found else ""


def run():
    s = Suite("An atelier on the guest's dates is offered")
    conn = db()
    _cleanup(conn)
    try:
        _checks(s, conn)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _checks(s, conn):
    now = _harness.datetime_now()
    # A quiet stretch more than a year out, clear of the house's own calendar.
    start = house_today() + timedelta(days=430)
    end = start + timedelta(days=4)
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity, active,
               sort_order, nights_label, created_at)
           VALUES (?, '', 2500, 8, 1, 95, 'Four nights', ?)""", (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 8, ?, ?)""", (wid, start.isoformat(), end.isoformat(), TAG, now))
    conn.commit()
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE workshop_id = ?", (wid,)).fetchone()["id"]
    room = _harness.ensure_room()
    anon = m.app.test_client()

    def book(a, d):
        return anon.get(f"/book?arrival={a.isoformat()}&departure={d.isoformat()}").get_data(as_text=True)

    s.section("On /book, after a search across its nights")
    page = book(start + timedelta(days=1), start + timedelta(days=3))
    card = _card(page)
    words = _text(card)
    s.check("the card is there, under the search", bool(card)
            and page.index('class="g-searched"') < page.index('class="g-onyour"'))
    s.check("it names the atelier and its dates",
            f"{TAG} Atelier" in words and m.format_date_range(start.isoformat(), end.isoformat()) in words
            and "Four nights" in words, detail=words[:200])
    s.check("what it includes, in the words its own letters use",
            # as the page typesets it, apostrophes curled (site review 25 Nov)
            m.atelier_included_words(conn.execute("SELECT * FROM workshops WHERE id = ?",
                                                  (wid,)).fetchone()).replace("'", "’") in words)
    s.check("its price and its places", "€2,500 per person" in words and "8 places left" in words,
            detail=words)
    reserve = re.search(r'href="([^"]+)">Reserve a place<', card)
    s.check("the way to take a place: its own registration form",
            reserve is not None and urlsplit(reserve.group(1)).path == f"/workshops/register/{sid}",
            detail=reserve.group(1) if reserve else "no Reserve a place")
    about = re.search(r'href="([^"]+)">About the atelier<', card)
    s.check("and the way to read about it", about is not None
            and urlsplit(about.group(1)).path == m.retreat_path(wid))
    s.check("the reserve link opens the form", reserve is not None
            and anon.get(reserve.group(1)).status_code == 200)
    s.check("the bare reason is not said twice above it",
            "a workshop holds the whole château" not in _text(page))
    s.check("the search line says an atelier has the château, not that no rooms are free",
            "An atelier has the château on these dates" in _text(page) and "No rooms free" not in _text(page))
    import os
    with open(os.path.join(m.app.root_path, "static", "gudanes.css"), encoding="utf-8") as fh:
        sheet = fh.read()
    s.check("in the room page's navy panel the card keeps its own ink, links included",
            ".g .g-onyour{ color: var(--ink); }" in sheet
            and ".g .g-dossier .g-onyour a:not(.g-btn){ color: var(--blue-deep); }" in sheet)
    around = re.findall(r'<a href="([^"]+)">(\d+ nights?, [^<]+)</a>', card)
    s.check("and the stays of their length either side of it", len(around) == 2, detail=str(around))
    for url, label in around:
        q = parse_qs(urlsplit(url.replace("&amp;", "&")).query)
        a, d = q.get("arrival", [""])[0], q.get("departure", [""])[0]
        s.check(f"'{label}' is two nights that do not touch the atelier's",
                (a, d) in ((( start - timedelta(days=2)).isoformat(), start.isoformat()),
                           (end.isoformat(), (end + timedelta(days=2)).isoformat())), detail=f"{a} {d}")
        landed = anon.get(url.replace("&amp;", "&")).get_data(as_text=True)
        s.check(f"'{label}' finds a room, rather than a second refusal",
                "No rooms free" not in landed and not _card(landed))

    s.section("Exactly on the nights it holds")
    s.check("a stay ending the day it begins: no card", not _card(book(start - timedelta(days=2), start)))
    s.check("a stay whose last night is its first: the card",
            bool(_card(book(start - timedelta(days=1), start + timedelta(days=1)))))
    s.check("a stay on its last night: the card",
            bool(_card(book(end - timedelta(days=1), end + timedelta(days=1)))))
    s.check("a stay from the morning its guests leave: no card",
            not _card(book(end, end + timedelta(days=2))))
    s.check("and no search at all: no card", not _card(anon.get("/book").get_data(as_text=True)))

    s.section("On a room's own page, and as its dates change")
    a, d = start + timedelta(days=1), start + timedelta(days=3)
    q = anon.get(f"/api/quote?room_id={room['id']}&arrival={a.isoformat()}&departure={d.isoformat()}").get_json()
    s.check("the quote says the nights are the atelier's",
            q and q["available"] is False and q["reason"] == m.ATELIER_NIGHTS_WORDS, detail=str(q and q["reason"]))
    s.check("and carries the card for the page to draw",
            q and q.get("ateliers") and q["ateliers"][0]["title"] == f"{TAG} Atelier"
            and q["ateliers"][0]["register_url"], detail=str(q and q.get("ateliers")))
    page = anon.get(f"/book/{room['id']}?arrival={a.isoformat()}&departure={d.isoformat()}").get_data(as_text=True)
    box = re.search(r'<div id="onyour_box"[^>]*>', page)
    s.check("the room page opens with the card drawn under its price",
            box is not None and "hidden" not in box.group(0) and f"{TAG} Atelier" in _card(page))
    s.check("the same card the page's script draws: one set of words",
            "drawOnYourDates(d.ateliers||[])" in page and "'Reserve a place'" in page
            and "'On your dates'" in page and "Reserve a place" in _card(page))
    other = anon.get(f"/book/{room['id']}?arrival={end.isoformat()}&departure="
                     f"{(end + timedelta(days=2)).isoformat()}").get_data(as_text=True)
    box = re.search(r'<div id="onyour_box"[^>]*>', other)
    s.check("and with other dates the box is there, empty and hidden, for the script",
            box is not None and "hidden" in box.group(0) and not _card(other))

    s.section("Not a place it cannot give")
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size, status,
               reference_code, manage_token, created_at) VALUES (?, ?, ?, 8, 'confirmed', ?, ?, ?)""",
        (sid, f"{TAG} Party", f"{TAG.lower()}@example.invalid", f"{TAG}-FULL",
         f"tok{TAG}{_harness.secrets_token()}".lower(), now))
    conn.commit()
    full = _card(book(start + timedelta(days=1), start + timedelta(days=3)))
    s.check("full: it says so, and offers no place", "This sitting is full" in _text(full)
            and "Reserve a place" not in full, detail=_text(full)[:200])
    s.check("and sends them to its other dates instead", "See its other dates" in full)
    conn.execute("DELETE FROM workshop_bookings WHERE session_id = ?", (sid,))
    conn.commit()
    with m.app.test_request_context("/"):
        under_way = m.ateliers_on_these_nights(conn, start + timedelta(days=1), start + timedelta(days=3),
                                               today=start + timedelta(days=1))
    s.check("under way: named, but no place, price or places offered",
            under_way and not under_way[0]["register_url"] and not under_way[0]["price"]
            and not under_way[0]["places"], detail=str(under_way))

    s.section("Never a private stay, nor a sitting called off")
    conn.execute("UPDATE workshops SET private_token = ? WHERE id = ?", (f"{TAG.lower()}key", wid))
    conn.commit()
    page = book(start + timedelta(days=1), start + timedelta(days=3))
    s.check("a private stay is not named or offered", not _card(page) and f"{TAG} Atelier" not in page)
    q = anon.get(f"/api/quote?room_id={room['id']}&arrival={(start + timedelta(days=1)).isoformat()}"
                 f"&departure={(start + timedelta(days=3)).isoformat()}").get_json()
    s.check("nor by the quote, which gives the plain refusal",
            q and not q.get("ateliers") and q["reason"] == m.GUEST_DATES_TAKEN, detail=str(q and q["reason"]))
    conn.execute("UPDATE workshops SET private_token = NULL WHERE id = ?", (wid,))
    conn.execute("UPDATE workshop_sessions SET cancelled_at = ? WHERE id = ?", (now, sid))
    conn.commit()
    s.check("a sitting called off holds nothing, and is offered nowhere",
            not _card(book(start + timedelta(days=1), start + timedelta(days=3))))

    s.section("One promise")
    import inspect
    s.check("the letters to atelier guests take what they include from the same sentence",
            "atelier_included_words(booking)" in inspect.getsource(m.workshop_email_context)
            and "every meal" not in inspect.getsource(m.workshop_email_context))
