# -*- coding: utf-8 -*-
"""The claw-foot bath, on the room pages that should show it (owner, 26 Nov 2026).

Every room page ended its photographs with two of the bathrooms: the long
bathroom under its chandelier, and the claw-foot bath against the blue floral
wallpaper. The owner, pointing at the bath: "take this off for the ground floor
twin, the twin, the king". Those three pages now end with the long bathroom
alone. The three suites keep both, and the Bathrooms section on /book, which is
every bathroom in the house rather than one room's, keeps it too.

Checked on the alt as well as the file name, so the photograph cannot come back
under another address (the photo mirror, a resized copy) without this noticing.
"""
from _harness import Suite, db
import _harness

m = _harness.m

BATH_FILE = "bath-floral-tiles"
BATH_ALT = "A claw-foot bath against the floral wallpaper"
LONG_ALT = "One of the ground-floor bathrooms"
WITHOUT = ("The Twin Room (Ground Floor)", "The Twin Room", "The King Room")
WITH = ("The King Suite", "The Marquis Suite", "The Family Suite")


def _strip(page):
    """The room's photographs: the carousel under the room's facts."""
    at = page.find('class="g-roomgal-car"')
    if at == -1:
        return ""
    end = page.find('class="g-tags"', at)
    return page[at:end if end != -1 else at + 200000]


def run():
    s = Suite("The claw-foot bath, on the room pages that should show it")
    conn = db()
    names = WITHOUT + WITH
    marks = ",".join("?" * len(names))
    # Only `active` is changed, so only `active` is put back. keep_rows would
    # delete the rooms and insert them again, and a room is not a row that can
    # be deleted: bookings refuse it, and its photographs and prices go with it
    # (ON DELETE CASCADE). On a database with a booking in one of these rooms
    # the put-back failed with its write still open, and every suite after
    # this one found the database locked.
    was_active = [(r["active"], r["id"]) for r in conn.execute(
        f"SELECT id, active FROM rooms WHERE name IN ({marks})", names)]

    def put_back():
        conn.executemany("UPDATE rooms SET active = ? WHERE id = ?", was_active)
        conn.commit()

    try:
        rooms = {r["name"]: r["id"] for r in conn.execute(
            f"SELECT id, name FROM rooms WHERE name IN ({marks})", names)}
        s.check("all six rooms are in the database", set(rooms) == set(names),
                detail=str(sorted(set(names) - set(rooms))))
        # A room off sale answers its page with a redirect; on sale for the read.
        conn.execute(f"UPDATE rooms SET active = 1 WHERE name IN ({marks})", names)
        conn.commit()

        with m.app.test_request_context("/"):
            mod = m.app.jinja_env.get_template("_room_photos.html").module
            listed = list(mod.NO_CLAWFOOT_BATH)
            bath = mod.BATH_PHOTOS[1]
        s.check("the list names the three rooms the owner named, and no other",
                sorted(listed) == sorted(WITHOUT), detail=str(listed))
        s.check("and the photograph it keeps off is the claw-foot bath",
                BATH_FILE in bath[0] and bath[1] == BATH_ALT, detail=str(bath))

        anon = m.app.test_client()
        for name in names:
            if name not in rooms:
                continue
            r = anon.get(f"/book/{rooms[name]}")
            page = r.get_data(as_text=True)
            strip = _strip(page)
            s.check(f"{name}: the page answers, with its photographs",
                    r.status_code == 200 and bool(strip), detail=str(r.status_code))
            s.check(f"{name}: the long bathroom is still in its photographs", LONG_ALT in strip)
            if name in WITHOUT:
                s.check(f"{name}: the claw-foot bath is nowhere on the page",
                        BATH_FILE not in page and BATH_ALT not in page)
            else:
                s.check(f"{name}: the claw-foot bath is still in its photographs",
                        BATH_FILE in strip and BATH_ALT in strip)

        book = anon.get("/book").get_data(as_text=True)
        at = book.find('<section class="g-sec g-baths-sec"')
        section = book[at:book.find("</section>", at)] if at != -1 else ""
        s.check("the Bathrooms section on /book still shows it, as one of every bathroom",
                bool(section) and BATH_FILE in section, detail="no Bathrooms section" if not section else "")
    finally:
        put_back()
        conn.commit()
        conn.close()
    return s
