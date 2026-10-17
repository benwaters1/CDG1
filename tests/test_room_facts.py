"""A room's beds and bathroom are asked once, and saving a room cannot lose them.

WHAT WENT WRONG (15 October 2026). The room form asked about the beds twice
(a "Beds" box and bed ticks) and which bathroom twice (a "Bathroom" box and
bathroom ticks), and the answers disagreed on the guest's own page: the Twin
and Queen rooms read "Private bathroom, on the ground floor" from the field,
above a "Shared bathroom" tag from the ticks.

Worse, the form could not hold what the rooms held. Its bathroom choices were
"En-suite", "Private, not en-suite" and "Shared", and every room held
"private" -- the value the guest pages read -- so the box opened on
"— choose —", and saving a room to change its price wiped its bathroom off the
booking cards. Two rooms' beds were not among the bed choices either. And a
ticked feature was saved as its key, which the guest pages print as it
stands: the first tick would have put "mountain_view" on a booking page.

WHAT THIS PINS: a room saved through its own form, as a browser sends it, keeps
its beds and bathroom; a tick is saved as words; a bed or a bathroom typed into
"anything else" is left out; and the owner's corrections of 15 October are on
the rooms, once, without undoing a later edit.
"""
from html.parser import HTMLParser

from _harness import Suite, clients, db, visible_text

import _harness

m = _harness.m
TAG = "ZZFACTS"


class _BrowserForm(HTMLParser):
    """What a browser would submit from the form posting to `action`: boxes as
    filled, ticked boxes only, and each select's selected option (or its first
    one, which is what a browser sends when none is marked)."""

    def __init__(self, action):
        super().__init__(convert_charrefs=True)
        self.action, self.inside, self.data = action, False, []
        self._select, self._first, self._chosen, self._text = None, None, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            # No action posts to the page itself, which is how the room form is written.
            self.inside = (a.get("action") or "") == (self.action or "")
        if not self.inside:
            return
        if tag == "input" and a.get("name"):
            kind = (a.get("type") or "text").lower()
            if kind in ("checkbox", "radio"):
                if "checked" in a:
                    self.data.append((a["name"], a.get("value", "on")))
            elif kind not in ("submit", "button", "file"):
                self.data.append((a["name"], a.get("value", "")))
        elif tag == "select" and a.get("name"):
            self._select, self._first, self._chosen = a["name"], None, None
        elif tag == "option" and self._select:
            if self._first is None:
                self._first = a.get("value", "")
            if "selected" in a:
                self._chosen = a.get("value", "")
        elif tag == "textarea" and a.get("name"):
            self._text = [a["name"], ""]

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False
        elif tag == "select" and self._select:
            self.data.append((self._select, self._chosen if self._chosen is not None
                              else (self._first or "")))
            self._select = None
        elif tag == "textarea" and self._text:
            self.data.append(tuple(self._text))
            self._text = None

    def handle_data(self, data):
        if self._text is not None:
            self._text[1] += data


def _as_a_browser_sends(html, action):
    p = _BrowserForm(action)
    p.feed(html)
    out = {}
    for name, value in p.data:
        out.setdefault(name, []).append(value)
    return out


def _room(conn, name):
    return conn.execute("SELECT * FROM rooms WHERE name = ?", (name,)).fetchone()


def run():
    s = Suite("a room's beds and bathroom, asked once and kept")
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    conn = db()
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    # Made through the owner's own form, then given what the live rooms hold:
    # 'private', a bed the choices lacked, and the old contradicting tags.
    oc.post("/admin/rooms/new", data={
        "name": TAG + " Suite", "description": "", "max_adults": "2",
        "max_children": "3", "price_per_night": "300", "min_nights": "1",
        "amenities_other": ""}, follow_redirects=True)
    conn.execute("""UPDATE rooms SET bed_setup = 'Queen and doubles', bathroom = 'private',
                        amenities = 'Mountain view, 2 bedrooms, Shared bathroom, Double bed'
                    WHERE name = ?""", (TAG + " Suite",))
    conn.commit()
    room = _room(conn, TAG + " Suite")
    s.check("the test room was made", room is not None)
    if room is None:
        return s
    action = f"/admin/rooms/{room['id']}/edit"

    try:
        s.section("The form holds what the room holds")
        page = oc.get(action).get_data(as_text=True)
        sent = _as_a_browser_sends(page, "")
        s.check("the bathroom box opens on the room's bathroom, not on '— choose —'",
                sent.get("bathroom") == ["private"], detail=str(sent.get("bathroom")))
        s.check("and the beds box on its beds, though they are not a usual choice",
                sent.get("bed_setup") == ["Queen and doubles"], detail=str(sent.get("bed_setup")))
        s.check("the beds and the bathroom are not also offered as ticks",
                not any(k in ("ensuite", "private_bathroom", "shared_bathroom", "king_bed",
                              "double_bed", "twin_beds", "emperor_bed")
                        for k in m.AMENITY_LABELS))

        s.section("Saving the room for something else keeps them")
        sent["price_per_night"] = ["320"]
        oc.post(action, data=sent, follow_redirects=True)
        after = _room(conn, TAG + " Suite")
        s.check("the price changed", after["price_per_night"] == 320, detail=str(after["price_per_night"]))
        s.check("the bathroom is still 'private', which the guest pages read",
                after["bathroom"] == "private", detail=repr(after["bathroom"]))
        s.check("and the beds are still the beds", after["bed_setup"] == "Queen and doubles",
                detail=repr(after["bed_setup"]))
        s.check("the old bed and bathroom tags came off on the way",
                "Shared bathroom" not in (after["amenities"] or "")
                and "Double bed" not in (after["amenities"] or ""),
                detail=repr(after["amenities"]))
        s.check("and the rest of what it has stayed",
                "Mountain view" in (after["amenities"] or "")
                and "2 bedrooms" in (after["amenities"] or ""),
                detail=repr(after["amenities"]))

        s.section("A tick is saved as words, because the guest pages print the list")
        sent = _as_a_browser_sends(oc.get(action).get_data(as_text=True), "")
        sent["amenities"] = sent.get("amenities", []) + ["fireplace", "sofa_bed"]
        sent["amenities_other"] = ["Shared bathroom, Pool view, Twin or double bedding"]
        oc.post(action, data=sent, follow_redirects=True)
        listed = _room(conn, TAG + " Suite")["amenities"] or ""
        s.check("a ticked feature is stored as its words, not its key",
                "Working fireplace" in listed and "fireplace" not in listed.replace("Working fireplace", ""),
                detail=repr(listed))
        s.check("'Sofa bed' is a feature, not the beds, and stays",
                "Sofa bed" in listed, detail=repr(listed))
        s.check("a bed or a bathroom typed into 'anything else' is left out",
                "Shared bathroom" not in listed and "bedding" not in listed
                and "Pool view" in listed, detail=repr(listed))
        s.check("and the ticks read back as ticked from the words",
                {"fireplace", "sofa_bed", "mountain_view"} <= m.room_amenity_keys(_room(conn, TAG + " Suite")),
                detail=str(m.room_amenity_keys(_room(conn, TAG + " Suite"))))

        s.section("The owner's corrections of 15 October are on the rooms")
        # The Queen Room is the King Room since 16 October (apply_room_names_2026_10_16).
        twin, queen, family = (_room(conn, n) for n in
                               ("The Twin Room", "The King Room", "The Family Suite"))
        s.check("the three rooms are there to be checked", twin and queen and family,
                detail="reported rather than skipped")
        if twin:
            s.check("the Twin Room's two beds are doubles, and it sleeps two",
                    twin["bed_setup"] == "Two double beds" and twin["max_occupancy"] == 2,
                    detail=f"{twin['bed_setup']!r}, sleeps {twin['max_occupancy']}")
        if queen:
            s.check("the King Room's bed (the old Queen Room's) is a king", queen["bed_setup"] == "King bed",
                    detail=repr(queen["bed_setup"]))
        if family:
            s.check("the Family Suite is two adults and three children",
                    (family["max_adults"], family["max_children"], family["max_occupancy"])
                    == (2, 3, 5),
                    detail=str((family["max_adults"], family["max_children"], family["max_occupancy"])))
        tagged = [r["name"] for r in conn.execute("SELECT name, amenities FROM rooms")
                  if any(m.says_bed_or_bathroom(t) and not m.amenity_key(t)
                         for t in (r["amenities"] or "").split(","))]
        s.check("no room's feature tags state its beds or its bathroom", not tagged,
                detail=", ".join(tagged))
        if twin:
            conn.execute("UPDATE rooms SET bed_setup = 'Twin or double' WHERE id = ?", (twin["id"],))
            conn.commit()
            again = m.apply_room_facts_2026_10_15(conn)
            kept = _room(conn, "The Twin Room")["bed_setup"]
            conn.execute("UPDATE rooms SET bed_setup = ? WHERE id = ?", (twin["bed_setup"], twin["id"]))
            conn.commit()
            s.check("and it is done once: a later edit by hand is never undone",
                    again == 0 and kept == "Twin or double", detail=f"changed {again}, bed {kept!r}")

        s.section("The guest's own page says one thing")
        if queen:
            text = visible_text(anon.get(f"/book/{queen['id']}").get_data(as_text=True))
            s.check("the King Room's page says a king bed and no queen bed",
                    "King bed" in text and "Queen bed" not in text and "queen bed" not in text,
                    detail=text[:200])
            s.check("and no 'Shared bathroom' under its private one",
                    "Shared bathroom" not in text, detail=text[:200])
        if twin:
            text = visible_text(anon.get(f"/book/{twin['id']}").get_data(as_text=True))
            s.check("the Twin Room's page says two double beds",
                    "Two double beds" in text, detail=text[:200])
    finally:
        conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
        conn.commit()
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
