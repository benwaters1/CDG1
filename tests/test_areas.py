"""Every page in the back end belongs to an area, and only one list says which.

The back end is 480-odd guarded pages. It has had areas all along — they decide
who may open what, and they group the sidebar — but the model was only half
wired, in two ways that fed each other.

  THE CONTENTS OF AN AREA WERE WRITTEN TWICE. Once in NAV_AREAS, which decides
  access, and again as hand-typed lists in base.html, which decided which menu
  lit up. They had drifted: 73 pages did not highlight their own area, 25 of
  them in Financial. You opened a page and the sidebar could not tell you where
  you were. The nav reads NAV_AREAS now, so the two cannot disagree again.

  181 PAGES WERE IN NO AREA AT ALL. can_reach refuses an unmapped endpoint,
  deliberately — a new page is private until somebody places it. But that makes
  "owner-only on purpose" and "nobody has got round to it" identical, so the
  second kind accumulated silently for months: the rota tools built for whoever
  runs the rota, the whole gallery, and the bulk buttons sitting on pages other
  people could already open. Somebody with the guests preset could confirm a
  booking one at a time, could see "Tie together as a party", and got a 403 from
  it.

So there are two lists now and a page must be in exactly one: NAV_AREAS if an
area may reach it, OWNER_ONLY_AREAS if only the owner may. Being in neither is
what this file exists to catch.
"""
import re

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZAREA"

ROUTE = re.compile(
    r"(@app\.route\(\s*\"[^\"]*\"[^)]*\)\s*\n(?:@[\w_]+(?:\([^)]*\))?\s*\n)*)def (\w+)")


def _guarded():
    src = open(m.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    return {name for deco, name in ROUTE.findall(src) if "owner_required" in deco}


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM access_presets WHERE slug LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()


def _preset_client(areas):
    """Somebody holding exactly these areas and nothing else."""
    from werkzeug.security import generate_password_hash
    slug = TAG.lower() + "p"
    email = TAG.lower() + "@example.invalid"
    conn = db()
    conn.execute("DELETE FROM users WHERE email = ?", (email,))
    conn.execute("DELETE FROM access_presets WHERE slug = ?", (slug,))
    conn.execute(
        """INSERT INTO access_presets (slug, name, description, areas,
           is_full_access, sort_order, created_at) VALUES (?, ?, 'probe', ?, 0, 99, ?)""",
        (slug, TAG + " preset", ",".join(areas), _harness.datetime_now()))
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status,
           created_at, access_preset) VALUES (?, ?, 'owner', ?, 'General', 'active', ?, ?)""",
        (email, generate_password_hash("probe-pw-123"), TAG + " Person",
         _harness.datetime_now(), slug))
    conn.commit()
    conn.close()
    c = m.app.test_client()
    c.post("/login", data={"email": email, "password": "probe-pw-123"},
           follow_redirects=True)
    return c


def run():
    s = Suite("Areas")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    guarded = _guarded()
    areas = set(m.ENDPOINT_AREA)
    owner_only = set(m.OWNER_ONLY_AREAS)

    s.section("Every guarded page has a home, and only one")
    s.check("there are pages to check", len(guarded) > 300, detail=f"{len(guarded)}")
    homeless = sorted(guarded - areas - owner_only)
    s.check("none is in neither list", not homeless,
            detail=f"{len(homeless)}: {homeless[:5]} — an unplaced page is "
                   "owner-only by accident, and looks exactly like one that is "
                   "owner-only on purpose")
    both = sorted(guarded & areas & owner_only)
    s.check("and none is in both", not both,
            detail=f"{both[:5]} — one of the two is then a lie")
    twice = [e for e in areas
             if sum(1 for eps in m.NAV_AREAS.values() if e in eps) > 1]
    s.check("no page belongs to two areas", not twice,
            detail=f"{twice[:5]} — whichever menu drew it last wins, which is "
                   "not a decision anybody made")

    # Every name has to BE something. Writing this list by hand, I left a
    # trailing comma off ten entries, and Python quietly joined each one to the
    # name below it -- "delete_rota_template_line" plus "bulk_tasks" became a
    # single endpoint called delete_rota_template_linebulk_tasks. It parses, it
    # imports, and it costs two pages their area: the joined name matches
    # nothing, and neither of the real ones is in the list any more.
    real = set(m.app.view_functions)
    unreal = sorted({e for eps in m.NAV_AREAS.values() for e in eps} - real)
    s.check("every name in an area is a real page", not unreal,
            detail=f"{unreal[:4]} — a name that matches no endpoint is silent: "
                   "nothing errors, the page just loses its area")
    unreal_owner = sorted(set(m.OWNER_ONLY_AREAS) - real)
    s.check("and so is every owner-only name", not unreal_owner,
            detail=f"{unreal_owner[:4]}")
    repeated = sorted({e for eps in m.NAV_AREAS.values()
                       for e in eps if eps.count(e) > 1})
    s.check("and no area lists the same page twice", not repeated,
            detail=f"{repeated[:4]} — harmless today, but it is what a missing "
                   "comma looks like once somebody adds the comma back")

    s.section("Only one list says what is in an area")
    base = open("templates/base.html", encoding="utf-8").read()
    leftovers = [n for n in re.findall(r"\{%\s*set (\w+)_endpoints", base)
                 if n != "house"]
    s.check("the nav keeps no hand-typed copy", not leftovers,
            detail=f"{leftovers} — this is the drift that left 73 pages unable "
                   "to highlight their own menu")
    # The House is a curated set of shortcuts ACROSS areas, not an area, so it
    # has nothing to read and is allowed to keep its own list.
    s.check("except The House, which is a shortcut menu and not an area",
            "house_endpoints" in base)

    s.section("A page knows which menu it is under")
    for path, want in (("/admin/city-tax", "financial"),
                       ("/admin/gallery", "comms"),
                       ("/admin/rota-clashes", "rota")):
        body = oc.get(path, follow_redirects=True).get_data(as_text=True)
        s.check(f"{path} highlights its area",
                "nav-dropdown-label active" in body,
                detail=f"expected {want} — before this, a fifth of the back end "
                       "highlighted nothing at all")

    s.section("Owner-only means owner-only, even holding that area")
    # The cash-up lives under the Till in the sidebar and is still refused to
    # somebody who only has the till. Where a page lives and who may open it
    # are different questions, and this is the check that keeps them apart.
    pc = _preset_client(["till", "guests", "team"])
    s.check("a till preset can still serve",
            pc.get("/pos").status_code in (200, 302),
            detail="the preset grants nothing at all, so the next check would "
                   "pass for the wrong reason")
    for path in ("/pos/day", "/admin/pennylane"):
        s.check(f"but {path} is refused",
                pc.get(path).status_code in (302, 403),
                detail=f"HTTP {pc.get(path).status_code}")
    s.check("and a private note on somebody's file is refused",
            pc.post("/directory/1/notes/new", data={"body": "x"}).status_code
            in (302, 403))

    s.section("The bulk buttons reach the people who can use the list")
    # The fault that started this: the single action was in an area and the
    # bulk button on the same page was in none, so it 403'd for anybody but the
    # owner while sitting in plain sight.
    for one, bulk in (("confirm_booking", "bulk_confirm_bookings"),
                      ("decline_booking", "bulk_decline_bookings"),
                      ("admin_bookings", "new_booking_party"),
                      ("admin_tasks", "bulk_tasks")):
        s.check(f"{bulk} sits with {one}",
                m.ENDPOINT_AREA.get(bulk) == m.ENDPOINT_AREA.get(one),
                detail=f"{m.ENDPOINT_AREA.get(bulk)} vs {m.ENDPOINT_AREA.get(one)}"
                       " — a button you can see and cannot press")

    s.section("Every area is named and reachable")
    s.check("every area has a title", not set(m.NAV_AREAS) - set(m.AREA_TITLES),
            detail=f"{sorted(set(m.NAV_AREAS) - set(m.AREA_TITLES))}")
    s.check("and every owner-only page's home is a real area",
            not {a for a in m.OWNER_ONLY_AREAS.values() if a} - set(m.NAV_AREAS),
            detail="a home nothing draws is a page that highlights nothing")

    _cleanup()
    return s
