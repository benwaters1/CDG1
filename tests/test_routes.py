"""Render every owner-visible GET page and report anything that breaks.

This is the cheapest regression net in the suite: it catches template and
context errors across the whole app, including on pages that have no seed
data, which is the usual source of a 500 that never appears in local clicking.

Routes that take an id are included by looking up a real row, because a page
is easy to break in a way only a populated record shows — a nav link to
/management/vehicles/<id>/transfers once 500'd the dashboard for exactly that
reason. Where no row exists the route is reported as skipped rather than
quietly passing.
"""
from _harness import Suite, clients, db

# Which table supplies a real id for each route parameter. Explicit rather
# than guessed from the name: doc_id means two different tables depending on
# the route, and a wrong guess would silently skip the route instead of
# testing it.
ID_SOURCES = {
    "booking_id": "bookings",
    "template_id": "campaign_templates",
    "code_id": "promo_codes",
    "room_id": "rooms",
    "workshop_id": "workshops",
    "user_id": "users",
    "expense_id": "expenses",
    "guest_id": "guests",
    "vehicle_id": "vehicles",
    "session_id": "workshop_sessions",
}
DOC_TABLE_BY_PREFIX = {"/management/documents/": "company_documents", "/documents/": "documents"}

# Tokens are per-record credentials; where the column is unambiguous we use a
# real one, so the guest-facing pages get covered too.
TOKEN_SOURCES = {
    "/book/manage/": ("bookings", "manage_token"),
    "/book/confirmation/": ("bookings", "manage_token"),
    "/checkin/": ("bookings", "manage_token"),
    "/workshops/manage/": ("workshop_bookings", "manage_token"),
    "/workshops/confirmation/": ("workshop_bookings", "manage_token"),
    "/restaurant/manage/": ("restaurant_bookings", "manage_token"),
    "/restaurant/confirmation/": ("restaurant_bookings", "manage_token"),
}

SKIP_WORDS = ("export", "backup", "logout", "download", "webhook", "static",
              "stripe-cancel", "pay-deposit", "pay-balance")


def _one(conn, table, column="id"):
    try:
        row = conn.execute(
            f"SELECT {column} AS v FROM {table} WHERE {column} IS NOT NULL LIMIT 1").fetchone()
    except Exception:
        return None
    return row["v"] if row else None


def _fill(path, conn):
    """Substitute a real value into a route pattern, or None if we can't."""
    import re
    import app as m
    # Reports are a fixed set, so they can be covered properly rather than
    # skipped. Read from the app's own registry so a new report is tested the
    # moment it is added, instead of quietly missing from a hardcoded list.
    if path == "/admin/reports/<slug>":
        slugs = sorted(getattr(m, "REPORT_BUILDERS", {}))
        return [f"/admin/reports/{slug}" for slug in slugs] or None
    for prefix, table in DOC_TABLE_BY_PREFIX.items():
        if path.startswith(prefix) and "doc_id" in path:
            v = _one(conn, table)
            return re.sub(r"<[^>]+>", str(v), path) if v is not None else None
    for prefix, (table, column) in TOKEN_SOURCES.items():
        if path.startswith(prefix):
            v = _one(conn, table, column)
            return re.sub(r"<[^>]+>", str(v), path) if v else None
    params = re.findall(r"<[^:>]*:?([a-z_]+)>", path)
    if len(params) != 1:
        return None
    table = ID_SOURCES.get(params[0])
    if not table:
        return None
    v = _one(conn, table)
    return re.sub(r"<[^>]+>", str(v), path) if v is not None else None


def run():
    import app as m
    s = Suite("Route sweep")
    oc, _ec, _owner, _emp = clients()

    plain, parameterised = [], []
    for rule in sorted(m.app.url_map.iter_rules(), key=lambda r: str(r)):
        path = str(rule)
        if "GET" not in rule.methods or any(w in path for w in SKIP_WORDS):
            continue
        (parameterised if "<" in path else plain).append(path)

    s.section(f"{len(plain)} pages with no parameters")
    broken = []
    for path in plain:
        try:
            code = oc.get(path).status_code
        except Exception as e:
            broken.append((path, f"{type(e).__name__}: {e}"))
            continue
        if code >= 500 or code not in (200, 302, 304, 404):
            broken.append((path, code))
    s.check(f"all {len(plain)} render", not broken,
            detail="; ".join(f"{p} -> {c}" for p, c in broken[:6]))

    conn = db()
    resolved, skipped = [], []
    for path in parameterised:
        filled = _fill(path, conn)
        if not filled:
            skipped.append((path, None))
        elif isinstance(filled, list):     # one pattern, several real URLs
            resolved += [(path, url) for url in filled]
        else:
            resolved.append((path, filled))
    conn.close()

    s.section(f"{len(resolved)} pages loaded with a real record")
    broken = []
    for path, filled in resolved:
        try:
            code = oc.get(filled).status_code
        except Exception as e:
            broken.append((filled, f"{type(e).__name__}: {e}"))
            continue
        if code >= 500 or code not in (200, 302, 304, 404):
            broken.append((filled, code))
    s.check(f"all {len(resolved)} render", not broken,
            detail="; ".join(f"{p} -> {c}" for p, c in broken[:6]))

    # Reported, never silent: a skipped route is untested coverage, not a pass.
    if skipped:
        print(f"    ....  {len(skipped)} skipped (no record to load, or a one-off token):")
        for path, _ in skipped:
            print(f"          {path}")
    return s
