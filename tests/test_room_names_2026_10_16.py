"""The rooms renamed on 16 October 2026, on the owner's word (handover z26).

The Queen Room has a king bed, so it is the King Room; the King Room with the
bath in it is the King Suite; the Gold Suite is the Marquis Suite. The Twin
Room and the Family Suite keep their names.

WHY IT IS A MIGRATION AND NOT A JOB FOR THE ROOM ADMIN. "The King Room"
changes meaning: it moves from the room with the bath to the old Queen Room.
The public templates match photographs and words to a room by its name, so
from the moment z26's templates are served until the rows are renamed, the
€380 room shows the other room's photographs. Renaming on the boot that first
serves them leaves no such moment -- as long as it runs once, in order, and
never again. Run twice by its old names, it would make the new King Room the
King Suite on the next deploy, which is the worst thing it could do and would
look like nothing at all.

Also held here, because the rename broke them and nothing errored:
  - the room picker on Stay read each bed from the old names ("king" meant the
    bath, "gold" the emperor), so it offered the King Room as the room with
    the bath and found no room at all for a king bed or an emperor;
  - Booking.com mail naming "King Room with Mountain View" found the new King
    Room by its own name, unless the King Suite carries that as its channel
    name.
"""
import io
import json
import os
import re

from _harness import Suite, db, keep_rows, visible_text
import _harness

m = _harness.m
FLAG = "room_names_2026_10_16"
NEW = [new for _old, new, _channel in m.ROOM_NAMES_2026_10_16]


def _rows(conn):
    return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM rooms")}


def _named(conn):
    return {r["name"]: dict(r) for r in conn.execute("SELECT * FROM rooms WHERE active = 1")}


def _reset(conn, ids, names):
    """Rows back to the given names (keyed by today's name), flag cleared."""
    for today, name in names.items():
        conn.execute("UPDATE rooms SET name = ? WHERE id = ?", (name, ids[today]))
    conn.execute("DELETE FROM app_settings WHERE key = ?", (FLAG,))
    conn.commit()


def run():
    s = Suite("The room names of 16 October")
    conn = db()
    rooms = _named(conn)

    s.section("init_db renamed the three")
    for name in NEW + ["The Twin Room", "The Family Suite"]:
        s.check(f"there is a room called {name}", name in rooms,
                detail=", ".join(sorted(rooms)))
    s.check("and none is left with the name it gave up",
            not ({"The Queen Room", "The Gold Suite"} & set(rooms)),
            detail=", ".join(sorted(rooms)))
    s.check("it records that it has run",
            conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (FLAG,)).fetchone()
            is not None, detail="without the flag it is keyed on names it has just reused")
    s.check("the order the site starts from uses the new names",
            set(m.HOME_ROOM_ORDER) <= set(rooms), detail=str(m.HOME_ROOM_ORDER))

    src = io.open(os.path.join(_harness.ROOT, "app.py"), encoding="utf-8").read()
    body = src[src.find("def init_db():"):]
    body = body[:body.find("\ndef ")]
    at = {name: body.find(name + "(conn)") for name in (
        "apply_room_lineup", "apply_room_facts_2026_10_15", "apply_room_views_2026_10_16",
        "apply_room_names_2026_10_16")}
    s.check("init_db renames AFTER the three that find rooms by their old names",
            -1 < at["apply_room_lineup"] < at["apply_room_names_2026_10_16"]
            and -1 < at["apply_room_facts_2026_10_15"] < at["apply_room_names_2026_10_16"]
            and -1 < at["apply_room_views_2026_10_16"] < at["apply_room_names_2026_10_16"],
            detail=str(at) + " -- before them, a fresh database keeps a Queen bed "
                             "and a Gold Suite with no view")

    before = _rows(conn)
    ids = {row["name"]: rid for rid, row in before.items() if row["active"]}
    if not all(n in ids for n in NEW):
        s.check("the three rooms are there to rename", False, detail=str(sorted(ids)))
        conn.close()
        return s
    as_on_the_15th = {new: old for old, new, _channel in m.ROOM_NAMES_2026_10_16}
    put_flag = keep_rows(conn, "app_settings", "key = ?", (FLAG,))
    probe_id = None
    try:
        s.section("In order, each row to its own new name")
        _reset(conn, ids, as_on_the_15th)
        conn.execute("UPDATE rooms SET channel_name = NULL WHERE id IN (?, ?)",
                     (ids["The King Suite"], ids["The King Room"]))
        conn.execute("UPDATE rooms SET channel_name = 'ZZ typed by hand' WHERE id = ?",
                     (ids["The Marquis Suite"],))
        conn.commit()
        changed = m.apply_room_names_2026_10_16(conn)
        after = _rows(conn)
        s.check("three rooms renamed", changed == 3, detail=str(changed))
        for name in NEW:
            s.check(f"the old {as_on_the_15th[name][4:]} is {name}",
                    after[ids[name]]["name"] == name, detail=after[ids[name]]["name"])
        s.check("so the room with the bath in it is the King Suite, not the King Room",
                after[ids["The King Suite"]]["name"] == "The King Suite"
                and after[ids["The King Room"]]["name"] == "The King Room")
        for keep in ("The Twin Room", "The Family Suite"):
            s.check(f"{keep} keeps its name", after[ids[keep]]["name"] == keep)
        s.check("the King Suite answers to Booking.com's 'King Room with Mountain View'",
                after[ids["The King Suite"]]["channel_name"] == "King Room with Mountain View",
                detail=repr(after[ids["The King Suite"]]["channel_name"]))
        s.check("the King Room to Booking.com's 'Queen'",
                after[ids["The King Room"]]["channel_name"] == "Queen",
                detail=repr(after[ids["The King Room"]]["channel_name"]))
        s.check("and a channel name typed by hand is kept",
                after[ids["The Marquis Suite"]]["channel_name"] == "ZZ typed by hand",
                detail=repr(after[ids["The Marquis Suite"]]["channel_name"]))
        s.check("each row kept its id, so its bookings and photographs follow it",
                set(after) == set(before))

        s.section("Once, and never by the names it has just reused")
        s.check("a second run renames nothing", m.apply_room_names_2026_10_16(conn) == 0)
        conn.execute("DELETE FROM app_settings WHERE key = ?", (FLAG,))
        conn.commit()
        changed = m.apply_room_names_2026_10_16(conn)
        name_now = conn.execute("SELECT name FROM rooms WHERE id = ?",
                                (ids["The King Room"],)).fetchone()["name"]
        s.check("nor one that has lost its flag: the King Room is never made the King Suite",
                changed == 0 and name_now == "The King Room",
                detail=f"{changed} renamed; the old Queen Room is now {name_now!r}")

        s.section("Left alone where it cannot be sure")
        # The owner renamed the room with the bath by hand before the deploy.
        _reset(conn, ids, {"The King Suite": "The King Suite",
                           "The King Room": "The Queen Room",
                           "The Marquis Suite": "The Gold Suite"})
        changed = m.apply_room_names_2026_10_16(conn)
        after = _rows(conn)
        s.check("a room the owner renamed first is left as named, and the rest follow",
                changed == 2 and after[ids["The King Suite"]]["name"] == "The King Suite"
                and after[ids["The King Room"]]["name"] == "The King Room"
                and after[ids["The Marquis Suite"]]["name"] == "The Marquis Suite",
                detail=f"{changed} renamed")
        # Two rows answering to one old name: renaming either is a guess.
        _reset(conn, ids, as_on_the_15th)
        probe_id = conn.execute(
            "INSERT INTO rooms (name, price_per_night, active, export_token) "
            "VALUES ('The Gold Suite', 1, 0, 'zz-names-probe')").lastrowid
        conn.commit()
        changed = m.apply_room_names_2026_10_16(conn)
        after = _rows(conn)
        s.check("an old name two rooms answer to renames neither",
                after[ids["The Marquis Suite"]]["name"] == "The Gold Suite"
                and after[probe_id]["name"] == "The Gold Suite" and changed == 2,
                detail=f"{changed} renamed; {after[ids['The Marquis Suite']]['name']!r}, "
                       f"{after[probe_id]['name']!r}")
    finally:
        if probe_id:
            conn.execute("DELETE FROM rooms WHERE id = ?", (probe_id,))
        for rid, row in before.items():
            conn.execute("UPDATE rooms SET name = ?, channel_name = ? WHERE id = ?",
                         (row["name"], row["channel_name"], rid))
        conn.commit()
        put_flag()
        conn.close()

    s.section("The pages put each room's words and photographs on the right room")
    anon = m.app.test_client()
    suite_html = anon.get(f"/book/{ids['The King Suite']}").get_data(as_text=True)
    room_html = anon.get(f"/book/{ids['The King Room']}").get_data(as_text=True)
    suite_page, room_page = visible_text(suite_html), visible_text(room_html)
    s.check("the King Suite's page says a bath and a basin in the room",
            "A bath and a basin of its own" in suite_page, detail=suite_page[:200])
    s.check("and the King Room's does not", "A bath and a basin of its own" not in room_page,
            detail=room_page[:200])
    s.check("the King Suite's page shows its four-poster",
            'alt="The King Suite, the four-poster and its canopy"' in suite_html)
    s.check("and the King Room's shows the old Queen Room, with no four-poster",
            'alt="The bed in the King Room"' in room_html and "four-poster" not in room_html)
    stay = anon.get("/book").get_data(as_text=True)

    def card(room_id):
        text = stay[stay.find(f'id="room-{room_id}"'):]
        return visible_text(text[:text.find("</article>")])

    s.check("on Stay, the King Room is the Chambre Olympe de Gouges, as the Queen Room was",
            "Olympe de Gouges" in card(ids["The King Room"])
            and "Olympe de Gouges" not in card(ids["The King Suite"]),
            detail=card(ids["The King Room"])[:200])
    s.check("and the King Suite is the four-poster with the bath",
            "four-poster canopy" in card(ids["The King Suite"]),
            detail=card(ids["The King Suite"])[:200])
    s.check("and the Marquis Suite the gilded one",
            "gilded four-poster" in card(ids["The Marquis Suite"]),
            detail=card(ids["The Marquis Suite"])[:200])

    s.section("The room picker reads each bed from the new names")
    html = anon.get("/book").get_data(as_text=True)
    line = re.search(r"r\.bed\s*=\s*(.+?);\s*\}\);", html)
    listed = re.search(r"var R=\[(.*?)\];", html, re.S)
    s.check("the Stay page carries the picker", bool(line and listed))
    if line and listed:
        rules = re.findall(r"/([^/]+)/\.test\(t\)\?'(\w+)'", line.group(1))
        names = [json.loads(n) for n in re.findall(r'n:("(?:[^"\\]|\\.)*")', listed.group(1))]

        def bed(name):
            return next((b for rx, b in rules if re.search(rx, name.lower())), "")

        wanted = {"The King Suite": "kingbath", "The King Room": "king", "The Twin Room": "twin",
                  "The Marquis Suite": "emperor", "The Family Suite": "family"}
        for name, b in wanted.items():
            if name in names:
                s.check(f"{name} is the room for '{b}'", bed(name) == b, detail=repr(bed(name)))
        offered = re.findall(r'name="pk_b" value="(\w+)"', html)
        lost = [b for b in offered if not any(bed(n) == b for n in names)]
        s.check("every bed a guest can choose finds a room", not lost,
                detail=f"no room for {lost} -- the guest is told to write in")
        twice = [b for b in offered if sum(bed(n) == b for n in names) > 1]
        s.check("and no bed finds two", not twice, detail=str(twice))

    s.section("Booking.com mail finds the room it names")
    conn = db()
    listing = [(r["id"], r["name"], r["channel_name"])
               for r in conn.execute("SELECT id, name, channel_name FROM rooms").fetchall()]
    conn.close()
    mail = ("Booking.com - New booking! (5979778151, Thursday, 29 October 2026)",
            "noreply@booking.com", "Booking.com", "",
            "Reservation number: 5979778151\nRoom: King Room with Mountain View\n"
            "Check-in: Thursday, 29 October 2026\nCheck-out: Saturday, 31 October 2026", [])
    got = m.parse_booking_com_email(*mail, rooms=listing)
    s.check("'King Room with Mountain View' is the King Suite",
            got["room_id"] == ids["The King Suite"],
            detail=f"room {got['room_id']} ({got['room_label']!r}); the King Suite is "
                   f"{ids['The King Suite']}, the King Room {ids['The King Room']}")
    bare = [(rid, name, None if rid == ids["The King Suite"] else channel)
            for rid, name, channel in listing]
    got = m.parse_booking_com_email(*mail, rooms=bare)
    s.check("which is the channel name's doing: without it the mail finds the King Room",
            got["room_id"] == ids["The King Room"],
            detail="if this stops being true, the channel name is no longer what matters")
    return s


if __name__ == "__main__":
    print(run().report())
