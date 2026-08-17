"""Access levels.

Every check here is about failing closed. A permissions bug does not announce
itself — the page simply opens for somebody who should not see it, and the first
sign is a member of staff knowing what everyone earns.

So: an unmapped page is owner-only, a deleted preset is not a skeleton key, an
account with no preset behaves exactly as it did before this existed, and the
last full-access account cannot be reduced.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZACC"


def _set_preset(user_id, slug):
    conn = db()
    conn.execute("UPDATE users SET access_preset = ? WHERE id = ?", (slug, user_id))
    conn.commit()
    conn.close()


def run():
    s = Suite("Access levels")
    oc, ec, owner, emp = clients()

    s.section("The levels are seeded")
    conn = db()
    presets = {r["slug"]: r for r in conn.execute("SELECT * FROM access_presets").fetchall()}
    conn.close()
    for slug in ("owner", "manager", "employee", "ben", "jasmine", "karina"):
        s.check(f"'{slug}' exists", slug in presets)
    s.check("owner has full access", bool(presets.get("owner", {})["is_full_access"]))
    s.check("employee grants no admin areas",
            (presets.get("employee", {})["areas"] or "") == "")

    s.section("Payroll is its own area, not bundled with the staff list")
    # Seeing who works here and seeing what they are paid are different levels
    # of trust. This was wrong first time round and is worth pinning.
    s.check("payroll is a separate area", "payroll" in m.NAV_AREAS)
    s.check("and is not inside team", "admin_payroll" not in m.NAV_AREAS.get("team", []))

    s.section("A partial level opens its own areas and refuses the rest")
    _set_preset(emp["id"], "jasmine")
    granted = [("/admin/restaurant", 200), ("/admin/events", 200)]
    refused = [("/admin/payroll", 403), ("/management/financials", 403),
               ("/management/vault", 403), ("/admin/audit-log", 403)]
    for path, expect in granted + refused:
        code = ec.get(path).status_code
        s.check(f"{path} -> {expect}", code == expect, detail=f"got {code}")

    s.section("The menu shows only what can be opened")
    html = ec.get("/today").get_data(as_text=True)
    s.check("a forbidden area is absent from the menu", "Payroll Pack" not in html)
    s.check("a granted area is present", "Restaurant" in html)

    s.section("Failing closed")
    conn = db()
    # An unmapped endpoint must not be reachable by a partial level.
    unmapped = [ep for ep in m.app.view_functions
                if ep not in m.ENDPOINT_AREA and ep.startswith("admin_")]
    conn.close()
    s.check("there are unmapped admin endpoints to check", bool(unmapped),
            detail="none found, so this check proves nothing")
    if unmapped:
        s.check("an unmapped admin page is owner-only",
                not m.can_reach({"role": "employee", "access_preset": "jasmine"}, unmapped[0]),
                detail=f"{unmapped[0]} was reachable")

    # A preset that has been deleted must not widen access.
    _set_preset(emp["id"], "no-such-preset")
    s.check("a missing preset grants nothing, not everything",
            ec.get("/admin/payroll").status_code == 403)

    _set_preset(emp["id"], None)
    s.check("no preset behaves as before it existed",
            ec.get("/admin/payroll").status_code == 403)
    s.check("and shared staff pages still work", ec.get("/today").status_code == 200)

    s.section("The owner level cannot be weakened")
    r = oc.post("/admin/access-levels/owner/save", data={"areas": ["guests"]},
                follow_redirects=True)
    conn = db()
    still_full = conn.execute(
        "SELECT is_full_access, areas FROM access_presets WHERE slug='owner'").fetchone()
    conn.close()
    s.check("saving over it is refused", still_full["is_full_access"] == 1, r)
    s.check("and its areas are untouched", still_full["areas"] == "*",
            detail=f"got {still_full['areas']!r}")

    s.section("The last full-access account cannot be reduced")
    r = oc.post("/admin/access-levels/assign",
                data={"user_id": str(owner["id"]), "access_preset": "karina"},
                follow_redirects=True)
    conn = db()
    unchanged = conn.execute(
        "SELECT access_preset FROM users WHERE id = ?", (owner["id"],)).fetchone()[0]
    conn.close()
    s.check("the change is refused", unchanged is None, r, detail=f"got {unchanged!r}")

    s.section("Only a full owner may change access")
    _set_preset(emp["id"], "karina")
    r = ec.post("/admin/access-levels/assign",
                data={"user_id": str(emp["id"]), "access_preset": "owner"})
    conn = db()
    escalated = conn.execute(
        "SELECT access_preset FROM users WHERE id = ?", (emp["id"],)).fetchone()[0]
    conn.close()
    # The obvious attack: use the page to give yourself everything.
    s.check("staff cannot promote themselves",
            r.status_code in (302, 403) and escalated != "owner",
            detail=f"HTTP {r.status_code}, preset now {escalated!r}")

    _set_preset(emp["id"], None)
    return s
