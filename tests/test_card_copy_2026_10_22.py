"""The card paragraphs on Workshops and Stay, as the owner wrote them (22 October 2026).

Only the description paragraph on each card changed; nights, dates, prices,
the feature lines and the buttons are as they were. The copy lives in
_ws_copy.html (ws_lead) and _room_copy.html (room_copy), which arrive from
the design side as whole files, so a handover that brings the old words back
goes red here.

Also held: no bathroom is ever called "ensuite"; the Marquis Suite's walls
are not mentioned on any page; and the view line reads "Mountain and front
views" for the Marquis Suite and the Family Suite, "Mountain view" for the
rest.
"""
import glob
import io
import os
import re

from _harness import Suite, db, visible_text
import _harness

m = _harness.m

WORKSHOPS = {
    "Immersive Artisan Workshops": "Four nights at the château, learning traditional crafts from people who still practise them.",
    "Noël at Gudanes": "The château’s first Noël atelier: four December nights as Gudanes fills with festive warmth.",
    "Cooking in the Cuisine": "Five nights in celebration of French cuisine.",
    "Antique & French Finds": "Three nights for the romance of the French brocante.",
    "Seven Starry Nights": "A week of long, golden summer days, balancing exploring with doing nothing at all.",
}
ROOMS = {
    "The Twin Room": "looking out to the mountains",
    "The Twin Room (Ground Floor)": "in a quiet room on the ground floor, though still reached by stairs",
    "The King Room": "kept safe while the room was brought back to life",
    "The King Suite": "the mountains beyond the windows",
    "The Marquis Suite": "The château’s grandest bedroom",
    "The Family Suite": "across 140 m²",
}
TWO_VIEWS = ("The Marquis Suite", "The Family Suite")


def run():
    s = Suite("Card copy, 22 October")

    s.section("Each workshop card says what the owner wrote")
    for title, opening in WORKSHOPS.items():
        with m.app.test_request_context("/"):
            said = m.app.jinja_env.from_string(
                "{% from '_ws_copy.html' import ws_lead %}{{ ws_lead(w) }}").render(
                w={"title": title, "nights_label": ""})
        s.check(title, said.startswith(opening), detail=said[:120])

    s.section("Each room card says what the owner wrote")
    for name, words in ROOMS.items():
        with m.app.test_request_context("/"):
            said = m.app.jinja_env.from_string(
                "{% from '_room_copy.html' import room_copy %}{{ room_copy(n) }}").render(n=name)
        s.check(name, words in said, detail=said[:120])

    s.section("And on the Stay page itself")
    page = m.app.test_client().get("/book").get_data(as_text=True)
    conn = db()
    rooms = conn.execute("SELECT id, name, outlook, amenities FROM rooms WHERE active = 1").fetchall()
    conn.close()
    for r in rooms:
        card = page[page.find(f'id="room-{r["id"]}"'):]
        card = visible_text(card[:card.find("</article>")])
        if r["name"] in ROOMS and ROOMS[r["name"]] not in card:
            s.check(f"{r['name']}'s card has its paragraph", False, detail=card[:160])
        # A card with typed features lists those instead of its own lines, the
        # view among them (the local copy has some; the live rooms did not).
        if r["outlook"] and not (r["amenities"] or "").strip():
            want = "Mountain and front views" if r["name"] in TWO_VIEWS else f"{r['outlook'].capitalize()} view"
            s.check(f"{r['name']} reads '{want}'", want in card, detail=card[:200])

    s.section("Never ensuite, and the Marquis Suite's walls nowhere")
    comment = re.compile(r"\{#.*?#\}|<!--.*?-->", re.S)
    root = os.path.join(_harness.ROOT, "templates")
    ensuite, walls = [], []
    for path in sorted(glob.glob(os.path.join(root, "*.html"))):
        text = comment.sub("", io.open(path, encoding="utf-8", errors="replace").read())
        name = os.path.basename(path)
        if re.search(r"\ben[- ]?suite\b", text, re.I):
            ensuite.append(name)
        if "within walls left as they were found" in text:
            walls.append(name)
    s.check("no page calls a bathroom ensuite", not ensuite, detail=", ".join(ensuite))
    s.check("no page describes the Marquis Suite's walls", not walls, detail=", ".join(walls))
    return s


if __name__ == "__main__":
    print(run().report())
