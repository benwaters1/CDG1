"""Getting Here: the journey from abroad, on one page (owner, 24 Nov 2026: yes).

Most guests come from far away -- the US, the UK, Australia -- and many do not
book because they cannot picture the journey. So /getting-here says it in
order: Paris, Toulouse, the château, by train, by air and car, our transfer,
a longer trip, and what to know about the mountain road.

What this holds:
  - the page renders, every table on it in a table-wrap;
  - it is linked from the menu, the footer, a retreat page, an occasion page
    and the booking confirmation letter;
  - the transfer price on it is the extras table's: change it, and the page
    follows;
  - the train times are cited to SNCF, never a timetable of our own.
"""
import re
from datetime import timedelta

from _harness import Suite, clients, db, ensure_room, keep_rows, visible_text
import _harness

m = _harness.m
TAG = "ZHERE"


def run():
    s = Suite("Getting Here")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    put_back = [keep_rows(conn, "extras", "category = 'transfer'")]
    try:
        _run(s, oc, conn)
    finally:
        conn.execute(f"DELETE FROM stay_occasion_prices WHERE occasion_id IN "
                     f"(SELECT id FROM stay_occasions WHERE name LIKE '{TAG}%')")
        conn.execute(f"DELETE FROM stay_occasions WHERE name LIKE '{TAG}%'")
        conn.execute(f"DELETE FROM bookings WHERE guest_name LIKE '{TAG}%'")
        conn.commit()
        for p in reversed(put_back):
            p()
        conn.close()
    return s


def _run(s, oc, conn):
    guest = m.app.test_client()
    s.section("The page")
    r = guest.get("/getting-here")
    html = r.get_data(as_text=True)
    text = visible_text(html)
    s.check("renders for a guest", r.status_code == 200)
    order = ["The journey in one line", "By train", "By air and car", "Our transfer",
             "Make a trip of it", "Good to know"]
    where = [text.find(h) for h in order]
    s.check("in the brief's order: the line, train, air and car, transfer, the trip, practical",
            all(w >= 0 for w in where) and where == sorted(where), detail=str(where))
    s.check("Les Cabannes, three minutes from the château",
            "Les Cabannes" in text and "three minutes" in text.lower())
    s.check("the train times are cited to SNCF Connect, with the date they were read",
            "sncf-connect.com/en-en/tgv-inoui/route/paris/toulouse" in html
            and "sncf-connect.com/train/trajet/toulouse/les-cabannes" in html
            and "read on 24 November 2026" in text)
    # 24 Nov 2026, merge: the retreats now meet at midday at the Grand Hôtel de l'Opéra (owner,
    # 23 Nov, on /workshops in claude/briefs-24nov). This page said 11am in central Toulouse and
    # would have contradicted /workshops the day both went live.
    s.check("the retreats' meeting: midday at the Grand Hôtel de l'Opéra, as /workshops says",
            "we meet at midday on the first day at the Grand Hôtel de l’Opéra, on Place du Capitole in Toulouse" in text
            and "central Toulouse" not in text
            and "11am" not in text.split("Our transfer")[-1].split("Make a trip of it")[0])
    s.check("no lift, and the stairs", "There is no lift" in text and "stairs" in text)
    tables = len(re.findall(r"<table\b", html))
    wrapped = len(re.findall(r'<div class="table-wrap">\s*<table\b', html))
    s.check("every table is in a table-wrap", tables and tables == wrapped,
            detail=f"{tables} tables, {wrapped} wrapped")
    s.check("no exclamation marks in the house voice",
            "!" not in re.sub(r"<!--.*?-->|<!doctype[^>]*>", "", text, flags=re.I | re.S))

    s.section("The transfer is the extras table's")
    x = conn.execute("SELECT * FROM extras WHERE category = 'transfer' AND active = 1 "
                     "AND guest_bookable = 1 ORDER BY sort_order, id LIMIT 1").fetchone()
    if not x:
        now = m.datetime.now(m.timezone.utc).isoformat()
        conn.execute("INSERT INTO extras (name, price, category, active, guest_bookable, created_at) "
                     "VALUES ('Airport Transfer (Toulouse, up to 3 guests)', 350, 'transfer', 1, 1, ?)", (now,))
        conn.commit()
        x = conn.execute("SELECT * FROM extras WHERE category = 'transfer' ORDER BY id DESC LIMIT 1").fetchone()
    s.check("the transfer shows at its price in the table",
            f"€{x['price']:,.0f}" in visible_text(guest.get("/getting-here").get_data(as_text=True)))
    conn.execute("UPDATE extras SET price = 412 WHERE id = ?", (x["id"],))
    conn.commit()
    after = visible_text(guest.get("/getting-here").get_data(as_text=True))
    s.check("change it in the table and the page follows", "€412" in after
            and (x["price"] == 412 or f"€{x['price']:,.0f}" not in after), detail=after[:200])

    s.section("Linked from everywhere a guest plans the journey")
    home = guest.get("/").get_data(as_text=True)
    menu = re.search(r'id="g-mainnav".*?</div>\s*<!--', home, re.S)
    s.check("from the menu, under Plan Your Visit",
            menu and 'href="/getting-here"' in menu.group(0), detail="not in the menu panel")
    foot = re.search(r"<h2>[^<]*Help[^<]*</h2>.*?</ul>", home, re.S)
    s.check("from the footer", foot and 'href="/getting-here"' in foot.group(0))
    w = conn.execute(f"""SELECT workshops.id FROM workshops
                          WHERE active = 1 AND {m.WORKSHOP_IS_PUBLIC} ORDER BY id LIMIT 1""").fetchone()
    retreat = guest.get(f"/workshops/{w['id']}", follow_redirects=True).get_data(as_text=True) if w else ""
    s.check("from a retreat page", 'href="/getting-here"' in retreat)
    room = ensure_room()
    now = m.datetime.now(m.timezone.utc).isoformat()
    day = m.house_today() + timedelta(days=400)
    occ_id = conn.execute(
        """INSERT INTO stay_occasions (name, slug, first_night, last_night, status, kind,
               description, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'live', 'stay', 'A few nights.', ?, ?)""",
        (f"{TAG} Week", f"{TAG.lower()}-week", day.isoformat(), (day + timedelta(days=2)).isoformat(),
         now, now)).lastrowid
    conn.execute("INSERT INTO stay_occasion_prices (occasion_id, room_id, price) VALUES (?, ?, 250)",
                 (occ_id, room["id"]))
    conn.commit()
    page = guest.get(f"/whats-on/at-gudanes/{TAG.lower()}-week").get_data(as_text=True)
    s.check("from an occasion page", 'href="/getting-here"' in page)
    conn.execute(
        """INSERT INTO bookings (room_id, guest_name, guest_email, arrival_date, departure_date, status,
               party_size, manage_token, reference_code, created_at)
           VALUES (?, ?, 'here.zhere@example.com', ?, ?, 'confirmed', 2, 'zhere-token', 'GUD-ZHERE', ?)""",
        (room["id"], f"{TAG} Guest", day.isoformat(), (day + timedelta(days=2)).isoformat(), now))
    conn.commit()
    booking = conn.execute("SELECT * FROM bookings WHERE reference_code = 'GUD-ZHERE'").fetchone()
    with m.app.test_request_context("/"):
        letter = m.booking_confirmation_html(conn, booking, room["name"])
    s.check("and from the booking confirmation letter",
            "/getting-here" in letter and "getting here" in letter, detail=letter[-400:] if letter else "no letter")


if __name__ == "__main__":
    print(run().report())
