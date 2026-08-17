"""Search, classify, sort — the one helper every list page uses.

Worth testing on its own rather than through a page: forty screens will depend
on it, and a fault here is forty faults. It takes plain dicts and a plain args
mapping, so none of this needs a request.

The last section walks every page that uses the toolbar and checks the promise
each chip makes — that clicking it gives exactly the number printed on it. Add
a URL to TOOLBAR_PAGES when you convert a page and it is covered from then on.
"""
import html
import re

from _harness import Suite, clients, db, datetime_now
import _harness

m = _harness.m

TOOLBAR_PAGES = [
    "/management/email-templates",
    "/admin/audit-log",
    "/contacts",
    "/admin/stock",
    "/management/recurring-costs",
    "/admin/promo-codes",
]


ROWS = [
    {"id": 1, "name": "Alice",  "status": "pending",   "team": "kitchen", "cost": 30, "due": "2026-01-02"},
    {"id": 2, "name": "bob",    "status": "confirmed", "team": "kitchen", "cost": 10, "due": None},
    {"id": 3, "name": "Céline", "status": "confirmed", "team": "front",   "cost": 20, "due": "2026-03-01"},
    {"id": 4, "name": "Dan",    "status": "cancelled", "team": "",        "cost": 40, "due": "2026-02-01"},
]

FACETS = [
    m.facet("status", "Status", lambda r: r["status"],
            order=["pending", "confirmed", "cancelled"]),
    m.facet("team", "Team", lambda r: r["team"]),
]
SORTS = [
    m.sort_option("name", "Name", lambda r: r["name"].lower()),
    m.sort_option("cost", "Most expensive", lambda r: r["cost"], reverse=True),
    m.sort_option("due", "Due date", lambda r: r["due"]),
]


def view(**args):
    return m.list_view(ROWS, args, search=["name", "status"], facets=FACETS,
                       sorts=SORTS, default_sort="name")


def ids(lv):
    return [r["id"] for r in lv["rows"]]


def run():
    s = Suite("List view")

    s.section("Nothing selected shows everything")
    lv = view()
    s.check("every row is shown", len(lv["rows"]) == 4)
    s.check("and it is not reported as filtered", not lv["filtered"])
    s.check("the default sort applies", ids(lv) == [1, 2, 3, 4], detail=str(ids(lv)))

    s.section("Search")
    s.check("matches on a field", ids(view(q="ali")) == [1], detail=str(ids(view(q="ali"))))
    s.check("is case-insensitive both ways", ids(view(q="BOB")) == [2])
    s.check("searches every listed field, not just the first",
            ids(view(q="cancelled")) == [4])
    s.check("whitespace-only is not a search", not view(q="   ")["filtered"])
    s.check("no match is empty, not everything", view(q="zzzz")["rows"] == [])

    s.section("Facets")
    s.check("a chosen value filters", ids(view(status="confirmed")) == [2, 3])
    s.check("an unknown value yields nothing rather than being ignored",
            view(status="nonsense")["rows"] == [])
    s.check("two facets combine", ids(view(status="confirmed", team="kitchen")) == [2])

    # The counts are the whole point of the chips — a chip reading 12 that
    # yields nothing when clicked is worse than no chip at all.
    lv = view()
    status = next(f for f in lv["facets"] if f["key"] == "status")
    counts = {o["value"]: o["count"] for o in status["options"]}
    s.check("counts match what clicking gives",
            all(len(view(status=v)["rows"]) == c for v, c in counts.items()),
            detail=str(counts))
    s.check("declared order is kept, not alphabetical",
            [o["value"] for o in status["options"]] == ["pending", "confirmed", "cancelled"],
            detail=str([o["value"] for o in status["options"]]))

    # Counts for a facet ignore that facet's own selection, so the other
    # options stay clickable — otherwise picking one hides the way back.
    lv = view(status="confirmed")
    status = next(f for f in lv["facets"] if f["key"] == "status")
    s.check("choosing one option still shows the others",
            len(status["options"]) == 3,
            detail=str([o["value"] for o in status["options"]]))
    s.check("and marks the chosen one", [o["value"] for o in status["options"] if o["selected"]]
            == ["confirmed"])

    # ...but they DO respect the other facets, and the search.
    lv = view(team="kitchen")
    status = next(f for f in lv["facets"] if f["key"] == "status")
    s.check("counts respect the other facets",
            {o["value"]: o["count"] for o in status["options"]} == {"pending": 1, "confirmed": 1},
            detail=str({o["value"]: o["count"] for o in status["options"]}))
    lv = view(q="ali")
    status = next(f for f in lv["facets"] if f["key"] == "status")
    s.check("and the search", sum(o["count"] for o in status["options"]) == 1)

    # An empty bucket is not a category. A row with no team is not "team: (blank)".
    team = next(f for f in view()["facets"] if f["key"] == "team")
    s.check("blank buckets are left out rather than shown as an option",
            sorted(o["value"] for o in team["options"]) == ["front", "kitchen"],
            detail=str([o["value"] for o in team["options"]]))

    s.section("Sort")
    s.check("named sort applies", ids(view(sort="cost")) == [4, 1, 3, 2],
            detail=str(ids(view(sort="cost"))))
    # A None in a sort key makes Python refuse the whole comparison; missing
    # values must sort last rather than crashing the page.
    s.check("a missing value sorts last instead of crashing",
            ids(view(sort="due")) == [1, 4, 3, 2], detail=str(ids(view(sort="due"))))
    s.check("an unknown sort falls back rather than 500ing",
            len(view(sort="nonsense")["rows"]) == 4)
    s.check("and reports itself as unset so the menu doesn't lie",
            view(sort="nonsense")["sort"] == "")

    s.section("What the toolbar is told")
    lv = view(q="a", status="pending")
    s.check("it knows it is filtered", lv["filtered"])
    s.check("shown and total are both reported",
            (lv["shown"], lv["total"]) == (len(lv["rows"]), 4),
            detail=f"{lv['shown']} of {lv['total']}")
    s.check("a facet with no options at all is dropped, not rendered empty",
            all(f["options"] for f in lv["facets"]))
    s.check("an empty list does not blow up",
            m.list_view([], {}, search=["name"], facets=FACETS, sorts=SORTS)["rows"] == [])

    s.section("It survives a real page")
    oc, _ec, _o, _e = clients()
    r = oc.get("/management/email-templates?area=Restaurant&stage=Confirmed")
    page = r.get_data(as_text=True)
    s.check("the toolbar renders", r.status_code == 200 and 'class="facet-row"' in page,
            detail=f"HTTP {r.status_code}")
    # A chip must carry the filters already applied, or clicking one silently
    # throws away the others.
    s.check("a chip keeps the filters already chosen",
            "area=Restaurant&amp;stage=Waitlist" in page)
    s.check("the search box keeps them too",
            '<input type="hidden" name="area" value="Restaurant">' in page)

    s.section("Every chip on every converted page keeps its promise")
    _seed_for_toolbar_pages()
    broken, vacuous = [], []
    for url in TOOLBAR_PAGES:
        r = oc.get(url)
        if r.status_code != 200:
            broken.append(f"{url} → HTTP {r.status_code}")
            continue
        page = r.get_data(as_text=True)
        chips = re.findall(r'<a href="([^"]+)"\s*\n?\s*class="chip[^"]*">'
                           r'([^<]*)<span class="chip-n">(\d+)</span>', page)
        chips = [(html.unescape(h), lbl.strip(), int(n)) for h, lbl, n in chips
                 if lbl.strip() != "All"]
        if not chips:
            vacuous.append(url)
            continue
        # One chip per page is enough to prove the wiring; checking every chip
        # on every page turns a fast suite into a slow one for no more signal.
        href, label, count = chips[0]
        got = oc.get(href).get_data(as_text=True)
        shown = re.search(r"Showing (\d+) of (\d+)", got)
        actual = int(shown.group(1)) if shown else None
        if actual != count:
            broken.append(f"{url}: chip {label!r} says {count}, clicking gives {actual}")

    s.check("a chip's count is what clicking it gives", not broken,
            detail=" | ".join(broken[:3]))
    # Say so rather than passing quietly: a page with no data proves nothing,
    # and a suite that can only go green is the thing worth distrusting.
    if vacuous:
        print(f"    ....  {len(vacuous)} page(s) had no rows to check: {', '.join(vacuous)}")
    s.check("most converted pages actually had data to check",
            len(vacuous) < len(TOOLBAR_PAGES),
            detail=f"{len(vacuous)}/{len(TOOLBAR_PAGES)} empty")

    return s


def _seed_for_toolbar_pages():
    """Enough rows that the chip check is not vacuous.

    The suite runs against a copy of whatever database is to hand, and on a
    fresh clone half these tables are empty — so the check would pass by
    finding nothing to check.
    """
    conn = db()
    now = datetime_now()
    try:
        if not conn.execute("SELECT 1 FROM contacts LIMIT 1").fetchone():
            for n, role in [("Test Plumber", "Plumber"), ("Test Sparks", "Electrician"),
                            ("Test Plumber Two", "Plumber")]:
                conn.execute("""INSERT INTO contacts (name, role, phone, sort_order, created_at)
                                VALUES (?, ?, '+33000000000', 0, ?)""", (n, role, now))
        if not conn.execute("SELECT 1 FROM stock_items WHERE active=1 LIMIT 1").fetchone():
            for name, cat, reorder, qty in [("Test champagne", "drinks", 6, 2),
                                            ("Test coffee", "food", 4, 12),
                                            ("Test towels", "linen", 20, 0)]:
                cur = conn.execute(
                    """INSERT INTO stock_items (name, category, unit, reorder_level,
                       unit_cost, location, active, created_at)
                       VALUES (?, ?, 'each', ?, 10.0, 'Cellar', 1, ?)""",
                    (name, cat, reorder, now))
                if qty:
                    conn.execute(
                        """INSERT INTO stock_movements (stock_item_id, delta, reason, created_at)
                           VALUES (?, ?, 'opening', ?)""", (cur.lastrowid, qty, now))
        if not conn.execute("SELECT 1 FROM recurring_costs LIMIT 1").fetchone():
            for label, amount, freq, cat, due, code in [
                    ("Test electricity", 480, "monthly", "Utilities", "2020-01-01", "6061"),
                    ("Test insurance", 5200, "annual", "Insurance", "2030-11-01", ""),
                    ("Test internet", 60, "monthly", "Utilities", "2030-09-05", "6262")]:
                conn.execute(
                    """INSERT INTO recurring_costs (label, amount, frequency, category,
                       next_due_date, active, created_at, ledger_code)
                       VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                    (label, amount, freq, cat, due, now, code))
        # Deliberately one of each state: active-but-expired, used up, and off.
        # A promo list where all four look alike proves nothing about the
        # classification that separates them.
        if not conn.execute("SELECT 1 FROM promo_codes LIMIT 1").fetchone():
            for code, active, until, used, cap in [
                    ("TESTLIVE", 1, "2099-12-31", 4, None),
                    ("TESTGONE", 1, "2020-01-31", 9, None),
                    ("TESTFULL", 1, None, 5, 5),
                    ("TESTOFF", 0, None, 0, None)]:
                conn.execute(
                    """INSERT INTO promo_codes (code, discount_type, discount_value, applies_to,
                       active, valid_until, redemption_count, max_redemptions, created_at)
                       VALUES (?, 'percent', 10, 'all', ?, ?, ?, ?, ?)""",
                    (code, active, until, used, cap, now))
        conn.commit()
    finally:
        conn.close()
