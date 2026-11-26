"""Three of the owner's notes on the site, 26 November 2026.

1. "dont say 6", then "say five". With six rooms on sale every "N guest rooms" on the site and in the letters said
   six. The count in words now stops at five (ROOM_COUNT_SAID_AT_MOST); "All the rooms are open from" says no number.
2. "this isnt right, its the room with all the plates on the wall". The second Then and Now pair's "before" is that
   room, not a bedroom: its "now" is the wall of plates (rk/china-dresser.jpg), not the King Room, on /restoration
   and /gallery, and /gallery shows that photograph once.
3. "size issue". The wallpaper pair on /restoration: a frame capped at 34rem beside a mount filling its column, so
   one was half the size of the other. In a .g-pair both fill their column, cut to the same 4:3.

Negative control: take the cap off house_room_facts and the first section goes red.
"""
import os
import re

from _harness import Suite, db, visible_text
import _harness

m = _harness.m


def _flat(text):
    return " ".join((text or "").split())


def _five(s, conn):
    s.section("1. Five guest rooms, however many are on sale")
    made = None
    active = conn.execute("SELECT COUNT(*) FROM rooms WHERE active = 1").fetchone()[0]
    try:
        if active < 6:
            made = conn.execute(
                "INSERT INTO rooms (name, description, max_occupancy, max_adults, max_children, price_per_night, "
                "active, sort_order, export_token) VALUES ('ZZNOTES Sixth Room', '', 2, 2, 0, 270, 1, 99, 'zz-notes-26')"
            ).lastrowid
            conn.commit()
        facts = m.house_room_facts(conn)
        s.check("six or more on sale", facts["count"] >= 6, detail=str(facts["count"]))
        s.check("the count in words is five", facts["count_word"] == "five", detail=facts["count_word"])
        said = m.merge_email_text("{Room_count_word} guest rooms are open", {})
        s.check("and the letters' tag says Five", said == "Five guest rooms are open", detail=said)
        page = _flat(visible_text(m.app.test_client().get("/restoration").get_data(as_text=True)))
        s.check("/restoration never says six guest rooms", not re.search(r"\bsix guest rooms\b", page, re.I))
        with m.app.test_request_context("/book"):
            strip = m.app.jinja_env.get_template("_availability_strip.html").make_module(
                {"house_rooms": {"count": 2, "count_word": "five"}}).next_free
            rows = [{"date": "28 November", "date_iso": "2026-11-28", "weekday": "Saturday", "room_name": "Room %d" % i,
                     "nights": 7, "price": 270} for i in range(2)]
            text = _flat(visible_text(str(strip(rows))))
        s.check("one date for every room: \"All the rooms are open\", with no number", "All the rooms are open from"
                in text, detail=text[:120])
    finally:
        if made:
            conn.execute("DELETE FROM rooms WHERE id = ?", (made,))
            conn.commit()


def _plates(s):
    s.section("2. The room with the plates on the wall, before and now")
    for path in ("/restoration", "/gallery"):
        html = m.app.test_client().get(path).get_data(as_text=True)
        pairs = re.findall(r'<div class="g-tn__pair" data-slider>(.*?)</div>', html, re.S)
        plates = [p for p in pairs if "The room with the plates on the wall" in p]
        s.check(f"{path}: the pair is the room with the plates on the wall", len(plates) == 1)
        s.check(f"{path}: and its now is that room's wall of plates, not the King Room",
                plates and "china-dresser.jpg" in plates[0] and "798764955" not in plates[0])
        s.check(f"{path}: no pair is called The bedrooms any more", "The bedrooms" not in " ".join(pairs))
    gallery = m.app.test_client().get("/gallery").get_data(as_text=True)
    shown = len(re.findall(r'<img\b[^>]*china-dresser(?:-\d+)?\.jpg', gallery))
    s.check("/gallery shows the wall of plates once, in the slider", shown == 1, detail=str(shown))


def _pair(s):
    s.section("3. The wallpaper pair, two of a size")
    css = open(os.path.join(_harness.ROOT, "static", "gudanes-pictures.css"), encoding="utf-8").read()
    s.check("in a pair the frame is not capped or centred",
            ".g .g-pair > .g-cadre, .g .g-pair > .g-pp{ max-width: none; margin: 0; }" in css)
    s.check("and both pictures are cut to 4:3",
            ".g .g-pair .g-cadre__img, .g .g-pair .g-pp__mount img{ aspect-ratio: 4 / 3; object-fit: cover; }" in css)
    html = m.app.test_client().get("/restoration").get_data(as_text=True)
    pair = re.search(r'<div class="g-pair">(.*?)</div>\s*</figure>\s*</div>|<div class="g-pair">(.*?)<div class="g-seeall',
                     html, re.S)
    s.check("/restoration's wallpaper is a pair of a frame and a mount", pair is not None
            and "g-cadre" in (pair.group(0)) and "g-pp" in pair.group(0))


def run():
    s = Suite("The owner's notes of 26 November 2026")
    conn = db()
    try:
        _five(s, conn)
        _plates(s)
        _pair(s)
    finally:
        conn.close()
    return s


if __name__ == "__main__":
    raise SystemExit(0 if run().report() else 1)
