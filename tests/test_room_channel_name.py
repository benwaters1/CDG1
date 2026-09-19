"""Two names for one room, because the channels do not use the house's.

The house calls it Chambre Emeraude. Booking.com lists it as whatever was
typed into the extranet years ago, usually in English. A booking arrives off
the feed under the second name and somebody has to translate it into the
first from memory, every time, and the person doing it is whoever happens to
be on that morning.

What is worth asserting is mostly about the ABSENCE. Almost every room will
have the two names the same, so the field will be blank on most rows, and a
blank that renders as an empty bracket or a dangling separator on every card
is worse than not having built it. So: blank saves as NULL, not as '', and a
room without one renders exactly what it rendered before.

And it stays on the staff side. A guest booking direct has no use for the
name a competitor's website uses, and it has no business on a public page.
"""
from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "ZZCHAN"
HOUSE = TAG + " Chambre Emeraude"
CHANNEL = TAG + " Double Room, Mountain View"


def _cleanup(conn):
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("the name the channels use")
    owner, employee, _owner_row, _emp_row = clients()
    conn = db()
    _cleanup(conn)

    s.section("It is saved from the form, not just held in the schema")
    # Through the ROUTE, because a column nothing writes to is not a feature.
    owner.post("/admin/rooms/new", data={
        "name": HOUSE, "channel_name": CHANNEL, "description": "",
        "max_adults": "2", "max_children": "0", "price_per_night": "200",
        "min_nights": "1", "amenities_other": "",
    }, follow_redirects=True)
    room = conn.execute("SELECT * FROM rooms WHERE name = ?", (HOUSE,)).fetchone()
    s.check("the room is created", room is not None)
    s.check("and carries the channel's name for it",
            room and room["channel_name"] == CHANNEL,
            detail=repr(room["channel_name"] if room else None))

    s.section("Blank means the names match, and stores as nothing")
    # Not ''. Every render below is guarded on truthiness, and an empty string
    # is falsey too -- but a NULL says "not set" where '' says "set to
    # nothing", and only one of those survives somebody sorting on the column.
    owner.post(f"/admin/rooms/{room['id']}/edit", data={
        "name": HOUSE, "channel_name": "   ", "description": "",
        "max_adults": "2", "max_children": "0", "price_per_night": "200",
        "min_nights": "1", "amenities_other": "", "active": "on",
    }, follow_redirects=True)
    s.check("whitespace saves as nothing at all",
            conn.execute("SELECT channel_name FROM rooms WHERE id = ?",
                         (room["id"],)).fetchone()["channel_name"] is None,
            detail="'' and NULL read the same on a page and differently "
                   "everywhere else")

    # Put it back for the rendering checks.
    owner.post(f"/admin/rooms/{room['id']}/edit", data={
        "name": HOUSE, "channel_name": CHANNEL, "description": "",
        "max_adults": "2", "max_children": "0", "price_per_night": "200",
        "min_nights": "1", "amenities_other": "", "active": "on",
    }, follow_redirects=True)
    s.check("and an edit puts it back",
            conn.execute("SELECT channel_name FROM rooms WHERE id = ?",
                         (room["id"],)).fetchone()["channel_name"] == CHANNEL,
            detail="the edit path and the create path are different SQL and "
                   "have drifted apart before")

    s.section("Both names, wherever staff read a room")
    rooms_page = owner.get("/admin/rooms").get_data(as_text=True)
    s.check("the rooms page shows the house name", HOUSE in rooms_page)
    s.check("and the channel name beside it", CHANNEL in rooms_page,
            detail="this is the page the Booking.com feed is configured on, "
                   "so it is where the two belong together")

    today = employee.get("/today").get_data(as_text=True)
    s.check("the Today board shows the house name", HOUSE in today)
    s.check("and the channel name beside it", CHANNEL in today,
            detail="an arrival off a feed should not need translating from "
                   "memory by whoever is on that morning")

    s.section("But a room without one renders exactly as before")
    # The case almost every room will be in. A blank that draws an empty
    # bracket or a dangling separator on every card is worse than no feature.
    owner.post("/admin/rooms/new", data={
        "name": TAG + " Plain", "channel_name": "", "description": "",
        "max_adults": "2", "max_children": "0", "price_per_night": "150",
        "min_nights": "1", "amenities_other": "",
    }, follow_redirects=True)
    plain = conn.execute("SELECT * FROM rooms WHERE name = ?",
                         (TAG + " Plain",)).fetchone()
    s.check("the plain room exists to check", plain is not None)
    page = owner.get("/admin/rooms").get_data(as_text=True)
    at = page.find(TAG + " Plain")
    after = page[at:at + 200] if at >= 0 else ""
    s.check("and nothing trails its name",
            "on the channels" not in after and "()" not in after,
            detail="the empty-bracket render is the one that would be on "
                   "every card in the house: " + repr(after[:80]))

    s.section("And it is not the guest's business")
    # A guest booking direct has no use for what a competitor's site calls
    # the room, and it would look like a mistake on a public page.
    public = m.app.test_client()
    for path, what in (("/book", "the public rooms page"),
                       (f"/book/{room['id']}", "the booking page")):
        body = public.get(path).get_data(as_text=True)
        # The room has to actually BE on the page first. Asserting an absence
        # against a page that never mentions the room at all passes for the
        # wrong reason and keeps passing after somebody adds the field to it.
        s.check("%s shows the room" % what, HOUSE in body,
                detail="otherwise the absence below is vacuous")
        s.check("%s does not show the channel name" % what, CHANNEL not in body,
                detail="staff-side vocabulary on a guest-facing page")

    s.section("The form offers it")
    # Read out of the rendered HTML rather than assumed: a field the route
    # accepts and the form never draws is a column nobody can fill in.
    form = owner.get(f"/admin/rooms/{room['id']}/edit").get_data(as_text=True)
    s.check("the edit form has the field",
            'name="channel_name"' in form,
            detail="the route accepting it is not the same as anybody being "
                   "able to type it")
    s.check("prefilled with what is stored", CHANNEL in form)
    new_form = owner.get("/admin/rooms/new").get_data(as_text=True)
    s.check("and so does the new-room form",
            'name="channel_name"' in new_form,
            detail="a field only on the edit page means every room is created "
                   "wrong and then fixed")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
