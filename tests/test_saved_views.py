"""The filters somebody sets every morning, kept.

Every filtered list in this app is already a URL — that was the design from the
start — so keeping one is only a matter of keeping the query string under a
name. Written once into the shared toolbar, so every list that includes it has
the feature without sixteen routes being touched.

Two things here are load-bearing:

  - PER PERSON AND PER PAGE. "Mine" on the rota is not "mine" on expenses, and a
    shared set would have two people overwriting each other. Somebody else's
    saved view is also not theirs to delete.
  - ONLY FILTER PARAMETERS ARE KEPT. Storing an arbitrary query string would
    turn this into a way to save any URL, including one carrying somebody's
    email address, which would then sit in a table forever under a name like
    "Tuesdays".
"""
import io
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZVIEW"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM saved_views WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _views(name_like=TAG):
    conn = db()
    try:
        return conn.execute("SELECT * FROM saved_views WHERE name LIKE ? ORDER BY id",
                            (name_like + "%",)).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("Saved views")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("A filtered list can be kept under a name")
    r = oc.post("/views/save", data={
        "endpoint": "admin_extras", "name": f"{TAG} On sale",
        "state": "On sale", "sort": "name",
    }, follow_redirects=True)
    saved = _views()
    s.check("it is stored", len(saved) == 1, detail=f"{flashes(r)[:1]}")
    s.check("against the page it came from",
            saved and saved[0]["endpoint"] == "admin_extras",
            detail=f"{saved[0]['endpoint'] if saved else None}")
    s.check("with the filters in it",
            saved and "state=On+sale" in saved[0]["query"],
            detail=f"{saved[0]['query'] if saved else None!r}")

    s.section("It appears on that list, and nowhere else")
    body = oc.get("/admin/extras").get_data(as_text=True)
    s.check("the chip is on the page it belongs to", f"{TAG} On sale" in body,
            detail="saved and then invisible is the same as not saved")
    other = oc.get("/admin/stock").get_data(as_text=True)
    s.check("and not on a different list", f"{TAG} On sale" not in other,
            detail="'mine' on the rota is not 'mine' on expenses")

    s.section("Following it puts the filters back")
    body = oc.get("/admin/extras?state=On+sale&sort=name").get_data(as_text=True)
    s.check("and it reads as the one in use", "chip is-on" in body,
            detail="a list of saved views with no indication which is showing "
                   "makes somebody click all of them to find out")

    s.section("The same name twice replaces rather than duplicates")
    oc.post("/views/save", data={
        "endpoint": "admin_extras", "name": f"{TAG} On sale",
        "state": "Withdrawn",
    }, follow_redirects=True)
    saved = _views()
    s.check("still one", len(saved) == 1, detail=f"{len(saved)}")
    s.check("carrying the newer filters",
            "Withdrawn" in saved[0]["query"],
            detail=f"{saved[0]['query']!r} — two views with one name is a list "
                   "nobody can use")

    s.section("Only filter parameters are kept")
    # The check that matters most. A saved view is a set of FILTERS; letting it
    # store any parameter makes it a way to save any URL.
    oc.post("/views/save", data={
        "endpoint": "admin_extras", "name": f"{TAG} Sneaky",
        "state": "On sale",
        "email": "someone@example.invalid",
        "token": "secret-value",
        "manage_token": "another-secret",
    }, follow_redirects=True)
    sneaky = [v for v in _views() if v["name"] == f"{TAG} Sneaky"]
    s.check("it saves", len(sneaky) == 1)
    s.check("the address is not in it",
            sneaky and "someone@example.invalid" not in sneaky[0]["query"],
            detail=f"{sneaky[0]['query'] if sneaky else None!r} — a table of "
                   "URLs with people's addresses in them, under names like "
                   "'Tuesdays'")
    s.check("nor a token",
            sneaky and "secret" not in sneaky[0]["query"],
            detail=f"{sneaky[0]['query'] if sneaky else None!r}")
    s.check("but the real filter survived",
            sneaky and "state=On+sale" in sneaky[0]["query"],
            detail=f"{sneaky[0]['query'] if sneaky else None!r} — a guard that "
                   "throws the filters away too has removed the feature")

    s.section("The stored query does not depend on how you got there")
    oc.post("/views/save", data={"endpoint": "admin_extras", "name": f"{TAG} A",
                                 "state": "On sale", "q": "wine"},
            follow_redirects=True)
    oc.post("/views/save", data={"endpoint": "admin_extras", "name": f"{TAG} B",
                                 "q": "wine", "state": "On sale"},
            follow_redirects=True)
    a = [v for v in _views() if v["name"] == f"{TAG} A"][0]
    b = [v for v in _views() if v["name"] == f"{TAG} B"][0]
    s.check("the same filters store the same string", a["query"] == b["query"],
            detail=f"{a['query']!r} vs {b['query']!r} — otherwise 'already "
                   "saved' is decided by the order somebody clicked things")
    # And that string is SORTED, not merely consistent. Comparing the two saves
    # against each other passed under any order that was applied to both, so it
    # was checking determinism and not the normalisation it claimed to.
    s.check("and it is in sorted order", a["query"] == "q=wine&state=On+sale",
            detail=f"{a['query']!r} — an order that happens to be stable today "
                   "stops being stable the moment a parameter is added")

    s.section("Forgetting one")
    r = oc.post(f"/views/{a['id']}/delete", follow_redirects=True)
    s.check("it goes", not any(v["id"] == a["id"] for v in _views()),
            detail=f"{flashes(r)[:1]}")

    s.section("Somebody else's is not yours to remove")
    mine = [v for v in _views() if v["name"] == f"{TAG} Sneaky"][0]
    r = ec.post(f"/views/{mine['id']}/delete", follow_redirects=False)
    s.check("refused", r.status_code == 404,
            detail=f"HTTP {r.status_code} — a 404 rather than a 403, so it does "
                   "not confirm one exists")
    s.check("and it is still there",
            any(v["id"] == mine["id"] for v in _views()))

    s.section("An employee has their own, not the owner's")
    ec.post("/views/save", data={"endpoint": "admin_extras",
                                 "name": f"{TAG} Theirs", "state": "Withdrawn"},
            follow_redirects=True)
    owner_page = oc.get("/admin/extras").get_data(as_text=True)
    s.check("the owner does not see it", f"{TAG} Theirs" not in owner_page,
            detail="one shared set has two people overwriting each other")

    s.section("A page that does not exist is refused")
    before = len(_views())
    r = oc.post("/views/save", data={"endpoint": "not_a_page",
                                     "name": f"{TAG} Nowhere"},
                follow_redirects=False)
    s.check("404", r.status_code == 404, detail=f"HTTP {r.status_code}")
    s.check("and nothing was stored", len(_views()) == before,
            detail="a saved view pinned to a page that does not exist is a link "
                   "that 500s from the toolbar of a page that does")

    s.section("A view with no name is refused")
    # Counted on the WHOLE table, not on the tagged rows: a view saved with an
    # empty name has no tag to match, so a tag-scoped count could never see the
    # thing it was checking for.
    conn = db()
    before = conn.execute("SELECT COUNT(*) c FROM saved_views").fetchone()["c"]
    conn.close()
    r = oc.post("/views/save", data={"endpoint": "admin_extras", "name": "  ",
                                     "state": "On sale"}, follow_redirects=True)
    conn = db()
    after = conn.execute("SELECT COUNT(*) c FROM saved_views").fetchone()["c"]
    nameless = conn.execute(
        "SELECT COUNT(*) c FROM saved_views WHERE TRIM(name) = ''").fetchone()["c"]
    conn.close()
    s.check("nothing stored", after == before, detail=f"{before} -> {after}")
    s.check("and no nameless row exists at all", nameless == 0,
            detail=f"{nameless} — a chip with no words on it cannot be clicked "
                   "off again")

    s.section("Every list has it, including the empty ones")
    # Written once. If it only worked on the page it was built against, the
    # feature would be a page rather than a capability — and it has to survive
    # an EMPTY list, because 13 of the 16 lists hide their toolbar when they
    # have no rows and that is exactly when somebody wants another view.
    conn = db()
    user = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
    for endpoint, url in (("admin_stock", "/admin/stock"),
                          ("contacts", "/contacts"),
                          ("management_vouchers", "/management/vouchers")):
        conn.execute(
            """INSERT INTO saved_views (user_id, endpoint, name, query, created_at)
               VALUES (?, ?, ?, 'q=zz', ?)""",
            (user["id"], endpoint, f"{TAG} {endpoint}",
             datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    for endpoint, url in (("admin_stock", "/admin/stock"),
                          ("contacts", "/contacts"),
                          ("management_vouchers", "/management/vouchers")):
        page = oc.get(url)
        s.check(f"{endpoint} shows its own",
                f"{TAG} {endpoint}" in page.get_data(as_text=True),
                detail=f"HTTP {page.status_code}")

    s.section("And nothing at all on a page with neither")
    src = io.open("templates/_saved_views.html", encoding="utf-8").read()
    s.check("the whole block is behind a guard",
            "{% if saved_views and saved_views|length or saved_view_current %}" in src,
            detail="an empty strip on every page in the app, including the ones "
                   "that are not lists")

    _cleanup()
    return s
