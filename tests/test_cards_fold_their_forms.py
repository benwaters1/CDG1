"""Cards say how a thing stands and fold the forms that change it; every box says what it is.

WHAT WENT WRONG. Rooms, Workshops and Vehicles drew every form on every card
open: four rooms each with an add-a-calendar form and a block-dates form, eight
workshops each with a six-box add-a-session form and a question form, a vehicle
with check-out, fault and insurance forms. The pages were mostly empty boxes,
and some of the boxes said nothing about themselves -- the two block dates had
no label at all (and the second is the day the room goes BACK on sale, which
nobody would guess), the insurance date did not say which date, a capacity box
announced itself as "Capacity ( default)", two money boxes as "42000" and
"6500". Deleting was a bare "×" a screen reader reads as "times", and editing a
vehicle was a span with an onclick that no keyboard could reach.

Four more pages had borrowed somebody else's layout: Reservations put its list
under a profit-share panel and a staffing form, Money ahead used the till's grid
and set what is going out one word to a line, the Shift Schedule used the
booking calendar's table and every person's row was the height of their name,
and its week bar stacked two buttons across the page. The Card reader said
Stripe was not connected twice, and Emails said letters "can go out once email
is connected" whatever had held them -- on the live site, Write to guests.

WHAT THIS PINS, on each page as the owner gets it.
"""
import re
from html.parser import HTMLParser

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "zzfold"
FAR = "2031-03-10"     # far enough ahead that nothing real is on it

PAGES = {
    "Rooms": "/admin/rooms",
    "Workshops": "/admin/workshops",
    "Vehicles": "/management/vehicles",
    "Reservations": "/admin/restaurant",
    "Money ahead": "/management/money-ahead",
    "Shift Schedule": "/admin/shifts",
    "Card reader": "/admin/terminal",
    "Emails": "/admin/emails",
}


class Boxes(HTMLParser):
    """Every box a person fills in inside <main>, and whether anything names it.
    Also every form, with whether it sits inside a closed <details>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_main = 0
        self.label_depth = 0
        self.labelled_ids = set()
        self.boxes = []          # (tag, name, id, aria-label, inside a <label>)
        self.forms = []          # (action, folded)
        self.details = []        # stack: open?
        self.buttons = []        # (text, aria-label)
        self._button = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "main":
            self.in_main += 1
        if not self.in_main:
            return
        if tag == "label":
            self.label_depth += 1
            if a.get("for"):
                self.labelled_ids.add(a["for"])
        elif tag == "details":
            self.details.append("open" in a)
        elif tag == "form":
            self.forms.append((a.get("action", ""), any(not o for o in self.details)))
        elif tag in ("input", "select", "textarea"):
            if tag == "input" and a.get("type", "text") in ("hidden", "submit", "button",
                                                             "image", "reset"):
                return
            self.boxes.append((tag, a.get("name", ""), a.get("id"), a.get("aria-label"),
                               self.label_depth > 0))
        elif tag == "button":
            self._button = [a.get("aria-label"), ""]

    def handle_endtag(self, tag):
        if tag == "main":
            self.in_main -= 1
        elif tag == "label" and self.label_depth:
            self.label_depth -= 1
        elif tag == "details" and self.details:
            self.details.pop()
        elif tag == "button" and self._button is not None:
            self.buttons.append((self._button[1].strip(), self._button[0]))
            self._button = None

    def handle_data(self, data):
        if self._button is not None:
            self._button[1] += data


def read(page):
    p = Boxes()
    p.feed(page)
    return p


def unnamed(p):
    """Boxes nothing names, and names that are not names."""
    bad = []
    for tag, name, id_, aria, wrapped in p.boxes:
        said = (aria or "").strip()
        if said and (re.fullmatch(r"[\d\s.,€]+", said) or re.search(r"\(\s*\)|\(\s+\w|\w\s+\)", said)):
            bad.append(f"{tag} {name}: aria-label {said!r} is not a name")
        elif not said and not wrapped and not (id_ and id_ in p.labelled_ids):
            bad.append(f"{tag} {name or '?'}")
    return bad


def _cleanup(conn):
    conn.execute("DELETE FROM room_blocks WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE ?", (TAG.upper() + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("cards fold their forms, and every box says what it is")
    oc, ec, owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()

    room = conn.execute("SELECT id, name FROM rooms ORDER BY id LIMIT 1").fetchone()
    conn.execute("INSERT INTO room_blocks (room_id, start_date, end_date, reason, created_at) "
                 "VALUES (?, ?, '2031-03-12', ?, ?)", (room["id"], FAR, TAG + " roof", now))
    # Something in stock, so each workshop draws its materials form.
    conn.execute("INSERT INTO stock_items (name, category, unit, active, created_at) "
                 "VALUES (?, 'food', 'kg', 1, ?)", (TAG + " flour", now))
    # More dinners than a page holds.
    conn.executemany(
        "INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, "
        "guest_email, party_size, dinner_date, status, created_at, payment_status) "
        "VALUES (?, ?, ?, ?, 2, ?, 'pending', ?, 'unpaid')",
        [(f"ZZF{i:03d}", f"{TAG}tok{i}", f"{TAG} Diner {i}", f"{TAG}{i}@example.invalid",
          f"2031-04-{1 + i % 28:02d}", now) for i in range(60)])
    # A letter held by the switch, the way the live site holds them.
    conn.execute("INSERT INTO email_outbox (to_address, subject, body, reason, attempts, "
                 "created_at) VALUES (?, ?, 'x', 'the house is not writing to guests yet', 0, ?)",
                 (f"{TAG}@example.invalid", TAG.upper() + " held", now))
    conn.commit()

    pages = {}
    for title, url in PAGES.items():
        r = oc.get(url)
        pages[title] = r.get_data(as_text=True)
        s.check(f"{title} draws", r.status_code == 200, detail=str(r.status_code))

    s.section("Every box says what it is")
    for title, page in pages.items():
        bad = unnamed(read(page))
        s.check(f"{title}: nothing to fill in goes unnamed", not bad, detail="; ".join(bad[:4]))
    s.check("the check can see an unnamed box",
            unnamed(read('<main><form><input type="date" name="x"></form></main>')) == ["input x"])
    s.check("and a name that is only a number, or a hole where a value was",
            len(unnamed(read('<main><input aria-label="42000"><input aria-label="Capacity ( default)">'
                             '</main>'))) == 2)

    s.section("A button that is only a mark says what it does")
    for title, page in pages.items():
        bare = [t for t, aria in read(page).buttons if t in ("×", "x", "✕") and not aria]
        s.check(f"{title}: no bare ×", not bare, detail=f"{len(bare)} of them")

    s.section("The forms that change a card are folded")
    folds = {
        "Rooms": (r"/ical-sources/new$", r"/blocks/new$"),
        "Workshops": (r"/sessions/new$", r"/custom-fields/new$", r"/materials$"),
        "Vehicles": (r"/checkout$", r"/maintenance/new$", r"/insurance/new$",
                     r"/vehicles/\d+/edit$", r"/vehicles/\d+/delete$"),
    }
    for title, actions in folds.items():
        forms = read(pages[title]).forms
        for act in actions:
            these = [folded for a, folded in forms if re.search(act, a)]
            s.check(f"{title}: every {act} form is folded",
                    these and all(these),
                    detail=f"{these.count(False)} of {len(these)} drawn open" if these
                    else "no such form on the page -- the check would pass on nothing")
    s.check("the vehicle edit is not a span with an onclick",
            "toggleVehicleEdit" not in pages["Vehicles"]
            and 'onclick="toggleVehicleEdit' not in pages["Vehicles"])

    s.section("A blocked room says which day it comes back")
    s.check("the form asks for the first night off and the day back on sale",
            "First night off sale" in pages["Rooms"] and "Back on sale from" in pages["Rooms"])
    s.check("and the block reads that way",
            f"Off sale from {m.format_date_short(FAR)}, back on sale "
            f"{m.format_date_short('2031-03-12')}" in pages["Rooms"])
    s.check("the calendar-link explanation is said once, not under every room",
            pages["Rooms"].count("calendar import") == 1,
            detail=f"{pages['Rooms'].count('calendar import')} times")

    s.section("Reservations open on the reservations")
    page = pages["Reservations"]
    at_list, at_share = page.find('class="list-search"'), page.find("Profit share")
    s.check("the list comes before the month's figures", 0 < at_list < at_share,
            detail=f"list at {at_list}, profit share at {at_share}")
    s.check("and pages rather than drawing every dinner", 'class="list-pager"' in page)
    s.check("with the dates as people read them",
            "2031-04-" not in page.split('class="list-search"', 1)[-1].split("Profit share")[0])

    s.section("Pages that borrowed another page's layout have their own")
    s.check("Money ahead does not use the till's grid",
            "pos-layout" not in pages["Money ahead"] and "money-sides" in pages["Money ahead"])
    s.check("the Shift Schedule's grid has room in it", "rota-grid" in pages["Shift Schedule"])
    s.check("and its week bar is not a search bar",
            'class="week-nav"' in pages["Shift Schedule"]
            and not re.search(r'class="search-bar"[^>]*>(?:(?!</form>).)*Copy last week',
                              pages["Shift Schedule"], re.S))
    css = open("static/style.css", encoding="utf-8").read()
    s.check("and the stylesheet gives those rows a height",
            re.search(r"\.cal-table\.rota-grid td\s*\{[^}]*height:\s*44px", css))

    s.section("A warning is said once, and for the right reason")
    # Either spelling of the apostrophe: the second box printed its message
    # through the escaper, as &#39;, and a plain count could not see it.
    said = len(re.findall(r"Stripe isn(?:'|&#39;|&#x27;)t connected", pages["Card reader"]))
    s.check("the card reader says Stripe is not connected once", said == 1,
            detail=f"{said} times")
    emails = pages["Emails"]
    boxes = emails.count('class="flash flash-error"')
    s.check("Emails has one warning box", boxes <= 1, detail=f"{boxes} boxes")
    s.check("which says a letter held by the switch is held by the switch",
            "because Write to guests is off" in emails)
    s.check("and no longer that everything waits for email to be connected",
            "once email is connected" not in emails)

    s.section("An employee sees none of it")
    for title, url in PAGES.items():
        s.check(f"{title} is not theirs", ec.get(url).status_code in (302, 403))

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
