"""Saving a room in the admin keeps what nobody changed.

The owner, 15 October 2026 (handover z17, C): saving Admin -> Rooms -> Edit
wiped the view, and a bed or bathroom the drop-downs did not list was lost.

Two faults, one shape. The form had no field for the view at all, so every
save wrote it blank. And the drop-downs offered "En-suite", "Private, not
en-suite" and "Shared" while the rooms said "private" and "shared" -- the
words the Stay cards, the room page and the comparison table read. A select
with nothing matching sends its first option, which was the blank one, so
the first save after any edit at all (a price, a photo) quietly emptied the
bathroom and the bed. The Stay card then stops saying the bathroom is
private, and the comparison table, which reads anything that is not exactly
"shared" as private, would call a shared bathroom private the day somebody
picked "Shared" with a capital S.

So this posts the form exactly as a browser would with nothing touched, and
reads the room back.
"""
from html.parser import HTMLParser

from _harness import Suite, clients, db
import _harness

m = _harness.m
KEPT = ("outlook", "bathroom", "bed_setup", "floor")


class _AsSubmitted(HTMLParser):
    """What a browser posts for each form on a page, untouched.

    The harness's FormReader answers "which fields does it draw"; this answers
    "what goes back when nobody changes anything", which needs the SELECTED
    option and the CHECKED boxes rather than the first of each.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.data = self.selects = None
        self._select = self._text = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.data, self.selects = [], {}
            self.forms.append((self.data, self.selects))
            return
        if self.data is None:
            return
        name = a.get("name")
        if tag == "input" and name:
            kind = (a.get("type") or "text").lower()
            if kind in ("checkbox", "radio"):
                if "checked" in a:
                    self.data.append((name, a.get("value") or "on"))
            elif kind not in ("file", "submit", "button", "image", "reset"):
                self.data.append((name, a.get("value") or ""))
        elif tag == "select" and name:
            self._select = {"name": name, "first": None, "picked": None, "options": []}
        elif tag == "option" and self._select is not None:
            value = a.get("value") or ""
            self._select["options"].append(value)
            if self._select["first"] is None:
                self._select["first"] = value
            if "selected" in a:
                self._select["picked"] = value
        elif tag == "textarea" and name:
            self._text = [name, ""]

    def handle_endtag(self, tag):
        if tag == "form":
            self.data = self.selects = None
        elif tag == "select" and self._select is not None:
            sel = self._select
            sent = sel["picked"] if sel["picked"] is not None else (sel["first"] or "")
            self.data.append((sel["name"], sent))
            self.selects[sel["name"]] = sel
            self._select = None
        elif tag == "textarea" and self._text is not None:
            self.data.append(tuple(self._text))
            self._text = None

    def handle_data(self, data):
        if self._text is not None:
            self._text[1] += data


class _Form:
    def __init__(self, data, selects):
        self.data, self.selects = data, selects


def as_submitted(html):
    """The room form -- the one with the beds in it -- as a browser would send it.

    The staff layout draws forms of its own above the content (the language
    switch, logging out), so "the first form" is not the room."""
    p = _AsSubmitted()
    p.feed(html)
    for data, selects in p.forms:
        if "bed_setup" in selects:
            return _Form(data, selects)
    return _Form([], {})


def _post(oc, room_id, data):
    form = {}
    for k, v in data:
        form.setdefault(k, []).append(v)
    return oc.post(f"/admin/rooms/{room_id}/edit", data=form, follow_redirects=False)


def run():
    s = Suite("Saving a room keeps what nobody changed")
    oc, _ec, _o, _e = clients()
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    if not room:
        s.check("there is a room to edit", False, detail="reported rather than skipped")
        conn.close()
        return s
    rid = room["id"]
    original = {k: room[k] for k in KEPT}

    def facts():
        return dict(conn.execute(
            f"SELECT {', '.join(KEPT)} FROM rooms WHERE id = ?", (rid,)).fetchone())

    def save_untouched():
        page = as_submitted(oc.get(f"/admin/rooms/{rid}/edit").get_data(as_text=True))
        return _post(oc, rid, page.data), page

    try:
        s.section("A room as the house describes it")
        conn.execute("UPDATE rooms SET outlook = 'mountain', bathroom = 'private', "
                     "bed_setup = 'Queen and doubles', floor = 'First floor' WHERE id = ?", (rid,))
        conn.commit()
        r, page = save_untouched()
        s.check("the form saves", r.status_code in (302, 303), detail=str(r.status_code))
        after = facts()
        s.check("the view survives a save nobody changed it in",
                after["outlook"] == "mountain", detail=str(after["outlook"]))
        s.check("so does a private bathroom", after["bathroom"] == "private",
                detail=str(after["bathroom"]))
        s.check("and a bed the drop-down does not list",
                after["bed_setup"] == "Queen and doubles", detail=str(after["bed_setup"]))
        s.check("and where it is", after["floor"] == "First floor", detail=str(after["floor"]))

        s.section("The bathroom choices are the words the booking pages read")
        options = set((page.selects.get("bathroom") or {}).get("options") or [])
        s.check("'private' and 'shared' are the choices, as the Stay cards spell them",
                {"private", "shared"} <= options, detail=str(sorted(options)))
        s.check("and nothing the guest pages would misread",
                not (options - {"", "private", "shared"}),
                detail=str(sorted(options - {"", "private", "shared"})))

        conn.execute("UPDATE rooms SET bathroom = 'shared' WHERE id = ?", (rid,))
        conn.commit()
        save_untouched()
        s.check("a shared bathroom stays shared", facts()["bathroom"] == "shared",
                detail=str(facts()["bathroom"]))

        s.section("A word the old drop-down wrote is kept, not blanked")
        conn.execute("UPDATE rooms SET bathroom = 'En-suite' WHERE id = ?", (rid,))
        conn.commit()
        save_untouched()
        s.check("an old value is still there after a save",
                facts()["bathroom"] == "En-suite", detail=str(facts()["bathroom"]))

        s.section("A change is saved, and the guest reads it")
        conn.execute("UPDATE rooms SET bathroom = 'private' WHERE id = ?", (rid,))
        conn.commit()
        page = as_submitted(oc.get(f"/admin/rooms/{rid}/edit").get_data(as_text=True))
        changed = [(k, "garden" if k == "outlook" else v) for k, v in page.data]
        _post(oc, rid, changed)
        s.check("the view can be changed from the form", facts()["outlook"] == "garden",
                detail=str(facts()["outlook"]))
        if room["active"]:
            stay = m.app.test_client().get("/book").get_data(as_text=True)
            s.check("and the Stay list says it", "Garden view" in stay,
                    detail="the card reads the outlook as '<word> view'")
    finally:
        conn.execute(f"UPDATE rooms SET {', '.join(k + ' = ?' for k in KEPT)} WHERE id = ?",
                     tuple(original[k] for k in KEPT) + (rid,))
        conn.commit()
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
