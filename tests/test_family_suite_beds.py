"""The Family Suite's beds and its photograph on Stay, as the owner gave them.

18 October 2026, with a screenshot of the room's bed options on Booking.com:
two double beds in one bedroom and three in the other. The site said "Queen
and doubles", the seed's guess. And "use a photo of the 2 beds in the room
with wallpaper new side by side, it's much nicer": the banner at the top of
Stay was the blue bedroom with its peeling paper.

Held here:
  - init_db gave the Family Suite its beds, and its page and Stay card say so;
  - once: a later edit in the room admin stands;
  - the room form offers the value, so a save keeps it;
  - Stay's banner is the two beds side by side. book_rooms.html arrives from
    the design side as a whole file, so a handover that brings the old banner
    back goes red here.
"""
from _harness import Suite, db, keep_rows, visible_text
import _harness

m = _harness.m
FLAG = "family_beds_2026_10_18"
TWO_BEDS = "799432953.jpg"


def run():
    s = Suite("The Family Suite's beds")
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE name = 'The Family Suite'").fetchone()
    s.check("there is a Family Suite", room is not None, detail="reported rather than skipped")
    if not room:
        conn.close()
        return s

    s.section("init_db gave it the owner's beds")
    s.check("two double beds in one bedroom, three in the other",
            room["bed_setup"] == m.FAMILY_SUITE_BEDS, detail=repr(room["bed_setup"]))
    s.check("and it records that it has run",
            conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (FLAG,)).fetchone() is not None)

    s.section("Once, so a later edit stands")
    put_flag = keep_rows(conn, "app_settings", "key = ?", (FLAG,))
    was = room["bed_setup"]
    try:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (FLAG,))
        conn.execute("UPDATE rooms SET bed_setup = 'Queen and doubles' WHERE id = ?", (room["id"],))
        conn.commit()
        s.check("the old guess is corrected", m.apply_family_beds_2026_10_18(conn) == 1
                and conn.execute("SELECT bed_setup FROM rooms WHERE id = ?",
                                 (room["id"],)).fetchone()["bed_setup"] == m.FAMILY_SUITE_BEDS)
        conn.execute("UPDATE rooms SET bed_setup = 'Bunk room' WHERE id = ?", (room["id"],))
        conn.commit()
        s.check("and a second run leaves an edit made since",
                m.apply_family_beds_2026_10_18(conn) == 0
                and conn.execute("SELECT bed_setup FROM rooms WHERE id = ?",
                                 (room["id"],)).fetchone()["bed_setup"] == "Bunk room")
    finally:
        conn.execute("UPDATE rooms SET bed_setup = ? WHERE id = ?", (was, room["id"]))
        conn.commit()
        put_flag()

    s.check("the room form offers it, so a save keeps it",
            m.FAMILY_SUITE_BEDS in m.BED_SETUPS)

    s.section("What a guest reads and sees")
    anon = m.app.test_client()
    page = visible_text(anon.get(f"/book/{room['id']}").get_data(as_text=True))
    s.check("the room's page states the beds", m.FAMILY_SUITE_BEDS in page, detail=page[:200])
    s.check("and no queen bed", "Queen and doubles" not in page)
    stay = anon.get("/book").get_data(as_text=True)
    card = stay[stay.find(f'id="room-{room["id"]}"'):]
    card = visible_text(card[:card.find("</article>")])
    # A card lists the room's typed features in place of its derived lines,
    # the beds among them, when it has any (the local copy's Family Suite does;
    # the live one did not on 18 October, and showed "Queen and doubles").
    if (room["amenities"] or "").strip():
        s.check("the Stay card, listing typed features, never says the old beds",
                "Queen and doubles" not in card, detail=card[:200])
    else:
        s.check("the Stay card states them too", m.FAMILY_SUITE_BEDS in card, detail=card[:200])
    hero = stay[stay.find('class="g-hero__img"'):]
    hero = hero[:hero.find(">")]
    s.check("Stay's banner is the two beds side by side under the new wallpaper",
            TWO_BEDS in hero, detail=hero[:200])
    s.check("not the blue bedroom with its peeling paper", "blue-bedroom" not in hero)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
