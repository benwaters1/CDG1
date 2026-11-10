"""Each room's own lines on its page, and the bathrooms on the ground floor (9 Nov 2026).

The owner: "room words is important make sure its french chateau romantic style
but accurate". _room_words.html holds them; every claim in it is one the owner
has confirmed (its header says where each comes from), and this holds the ones
that have gone wrong before or would be easy to get wrong:
  - each room's words are about that room: the name it goes by, its bed
  - a four-poster only in the two rooms that have one (owner, 14 Oct)
  - never "ensuite"; never the Marquis Suite's walls; no mountain view from the
    Marquis Suite, which faces the front of the chateau
  - on the page, under the room's name and above its facts

And the stock descriptions said every private bathroom was on the first floor.
It is on the ground floor, as every room page says; the stored copies are put
right once, and an owner's own description is never touched.
"""
import re

from _harness import Suite, db, visible_text
import _harness

m = _harness.m

# What each room's words must say, from _room_words.html's own sources.
MUST = {
    "The Twin Room": ("Chambre Jeanne d’Arc", "gilded beds", "tapestry", "2021", "mountains"),
    "The King Room": ("Chambre Olympe de Gouges", "king bed", "slate floor", "floor and ceiling survived", "mountains"),
    "The King Suite": ("king bed", "wooden four-poster", "bath and a basin", "mountains"),
    "The Marquis Suite": ("grandest bedroom", "gilded four-poster", "emperor bed", "70 m²", "fireplace"),
    "The Family Suite": ("Chambre Bleue", "Chambre des Fleurs", "140 m²", "children of eight and over", "wallpaper"),
    "The Twin Room (Ground Floor)": ("queen bed and a double bed", "ground floor", "reached by stairs", "next door"),
}
FOUR_POSTERS = ("The King Suite", "The Marquis Suite")


def _words(name):
    with m.app.app_context():
        module = m.app.jinja_env.get_template("_room_words.html").module
        return " ".join(str(module.room_words(name)).split())


def run():
    s = Suite("Room words")

    s.section("Every room has its words, and they are about that room")
    words = {name: _words(name) for name in MUST}
    for name, needs in MUST.items():
        missing = [n for n in needs if n not in words[name]]
        s.check(f"{name}: its words say what it is", words[name] and not missing,
                detail=f"missing {missing}: {words[name][:120]}")

    s.section("Nothing in them the house has not confirmed")
    s.check("a four-poster only where there is one: the King Suite and the Marquis Suite",
            all(("four-poster" in w) == (n in FOUR_POSTERS) for n, w in words.items()),
            detail=", ".join(n for n, w in words.items() if ("four-poster" in w) != (n in FOUR_POSTERS)))
    s.check("no bathroom is ever called ensuite",
            not any(re.search(r"en[- ]?suite", w, re.I) for w in words.values()))
    s.check("the Marquis Suite's walls are not mentioned", "wall" not in words["The Marquis Suite"].lower())
    s.check("and no mountain view is promised from it: it faces the front of the château",
            "mountain" not in words["The Marquis Suite"].lower() and "front of the château" in words["The Marquis Suite"])
    s.check("no bathroom on the first floor, in any of them",
            not any("first floor" in w.lower() for w in words.values()))
    s.check("the bed, the bathroom and breakfast are left to the facts below: no price, no times",
            not any(re.search(r"€\s?\d|\d\s?(am|pm)\b|breakfast", w, re.I) for w in words.values()))

    s.section("On each room's page, under its name and above its facts")
    client = m.app.test_client()
    conn = db()
    rooms = conn.execute("SELECT id, name FROM rooms WHERE active = 1 ORDER BY sort_order").fetchall()
    conn.close()
    s.check("there are rooms on sale to look at", len(rooms) >= 5, detail=str(len(rooms)))
    for room in rooms:
        r = client.get(f"/book/{room['id']}")
        page = r.get_data(as_text=True)
        found = re.search(r'<p class="g-p g-intro g-room__words">(.*?)</p>', page, re.S)
        shown = " ".join(_harness.m.html_unescape(found.group(1)).split()) if found else ""
        s.check(f"{room['name']}: its words are on its page", r.status_code == 200 and shown == words.get(room["name"]),
                detail=f"{r.status_code} {shown[:80]!r}")
        h1, at, dl = page.find("<h1"), page.find("g-room__words"), page.find('<dl class="g-glance">')
        s.check(f"{room['name']}: under its name, above its facts", -1 < h1 < at < dl, detail=f"{h1} {at} {dl}")
        s.check(f"{room['name']}: nothing on its page puts a bathroom on the first floor",
                "first floor" not in visible_text(page).lower())

    s.section("The stored descriptions: the bathrooms on the ground floor, once")
    s.check("none of the stock words says first floor any more",
            not any("first floor" in w for w in m.ROOM_LINEUP_WORDS.values()))
    s.check("there is an old sentence for each of the five stock ones to put right",
            len(m.ROOM_BATH_FLOOR_2026_11_09) == 5
            and all("first floor" in old and "ground floor" in new and old.replace("first floor", "ground floor") == new
                    for old, new in m.ROOM_BATH_FLOOR_2026_11_09.items()))
    conn = db()
    rows = conn.execute("SELECT id, name, description FROM rooms ORDER BY id").fetchall()
    before = {r["id"]: r["description"] for r in rows}
    flag = conn.execute("SELECT value FROM app_settings WHERE key = 'room_bathroom_floor_2026_11_09'").fetchone()
    old_twin = m.ROOM_LINEUP_WORDS["The Twin Room"].replace("ground floor", "first floor")
    own = "The owner's own words: tea on the first floor landing."
    a, b = rows[0]["id"], rows[1]["id"]
    try:
        conn.execute("UPDATE rooms SET description = ? WHERE id = ?", (old_twin, a))
        conn.execute("UPDATE rooms SET description = ? WHERE id = ?", (own, b))
        conn.execute("DELETE FROM app_settings WHERE key = 'room_bathroom_floor_2026_11_09'")
        conn.commit()
        changed = m.apply_room_bathroom_floor_2026_11_09(conn)
        now = {r["id"]: r["description"] for r in conn.execute("SELECT id, description FROM rooms")}
        s.check("a stock sentence still saying first floor is put right", now[a] == m.ROOM_LINEUP_WORDS["The Twin Room"],
                detail=now[a])
        s.check("a description the owner wrote is left exactly as written", now[b] == own, detail=now[b])
        s.check("and only the one changed", changed == 1, detail=str(changed))
        conn.execute("UPDATE rooms SET description = ? WHERE id = ?", (old_twin, a))
        conn.commit()
        s.check("it runs once: a second boot changes nothing", m.apply_room_bathroom_floor_2026_11_09(conn) == 0
                and conn.execute("SELECT description FROM rooms WHERE id = ?", (a,)).fetchone()[0] == old_twin)
    finally:
        for rid, desc in before.items():
            conn.execute("UPDATE rooms SET description = ? WHERE id = ?", (desc, rid))
        conn.execute("DELETE FROM app_settings WHERE key = 'room_bathroom_floor_2026_11_09'")
        if flag:
            conn.execute("INSERT INTO app_settings (key, value) VALUES ('room_bathroom_floor_2026_11_09', ?)", (flag[0],))
        conn.commit()
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
