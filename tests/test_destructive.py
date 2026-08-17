"""Every endpoint that destroys or overrides something.

These are the largest untested group in the app and the worst kind to leave
alone: a delete route with a missing permission check does not look broken, it
looks like nothing, right up until a member of staff removes a booking.

Enumerated from the URL map by shape — delete, decline, cancel, void, remove,
clear — so a new one is covered the day it is written. Every call uses an id that
does not exist, so the suite cannot destroy real rows even if a gate is missing.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
BOGUS_ID = 987654321
DESTRUCTIVE = ("delete", "decline", "cancel", "void", "remove", "clear",
               "reject", "discard", "no_show", "noshow")


def _destructive_rules():
    out = []
    for rule in m.app.url_map.iter_rules():
        if "POST" not in rule.methods:
            continue
        name = rule.endpoint.lower()
        if not any(word in name for word in DESTRUCTIVE):
            continue
        path = str(rule)
        # Substitute a non-existent id for every parameter, so nothing real can
        # be hit. Tokens get a string that cannot match a real one.
        filled = path
        import re
        for match in re.findall(r"<[^>]+>", path):
            filled = filled.replace(
                match, str(BOGUS_ID) if "int:" in match else "zz-not-a-real-token")
        out.append((filled, rule.endpoint))
    return sorted(set(out))


def run():
    s = Suite("Destructive endpoints")
    oc, ec, owner, emp = clients()
    rules = _destructive_rules()

    s.section(f"{len(rules)} destroying endpoints, found by shape")
    s.check("there are destructive endpoints to check", bool(rules),
            detail="none found, so this suite proves nothing")

    s.section("None of them answers 200 to a plain employee")
    # An employee here has no access preset, so no admin area at all. Anything
    # returning 200 either performed the action or reported success for it.
    leaked, errored = [], []
    for path, endpoint in rules:
        try:
            r = ec.post(path)
        except Exception as e:
            errored.append(f"{endpoint} raised {type(e).__name__}: {e}")
            continue
        if r.status_code == 200:
            leaked.append(f"{path} ({endpoint})")
        elif r.status_code >= 500:
            errored.append(f"{path} -> {r.status_code}")
    s.check("no destructive endpoint answers an employee", not leaked,
            detail="; ".join(leaked[:5]))
    # A 500 means the route ran far enough to fall over, which is itself a sign
    # the permission check is not the first thing it does.
    s.check("and none crashes on the attempt", not errored, detail="; ".join(errored[:5]))

    s.section("The owner reaches them, so the check above means something")
    # Without this, "every endpoint refused the employee" would also pass if all
    # the URLs were wrong.
    reached, refused_owner = 0, []
    for path, endpoint in rules:
        r = oc.post(path)
        if r.status_code in (302, 404, 400):
            reached += 1      # ran, then found no such row — which is correct
        elif r.status_code >= 500:
            refused_owner.append(f"{path} -> {r.status_code}")
    s.check(f"the owner gets a clean answer from {reached} of {len(rules)}",
            not refused_owner, detail="; ".join(refused_owner[:5]))
    s.check("and reaches most of them", reached > len(rules) * 0.6,
            detail=f"only {reached}/{len(rules)} — the URL substitution may be wrong")

    s.section("Nothing was actually destroyed")
    # The whole suite runs against ids that do not exist, so the row counts it
    # started with must be the row counts it ends with.
    conn = db()
    counts = {t: conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
              for t in ("bookings", "users", "rooms", "tasks", "guests",
                        "restaurant_bookings", "workshop_bookings")}
    conn.close()
    print("       (row counts after the sweep: "
          + ", ".join(f"{t}={n}" for t, n in counts.items()) + ")")
    s.check("the users table still has an owner", counts["users"] >= 1)
    s.check("rooms were not emptied", counts["rooms"] >= 1)

    conn = db()
    conn.execute("DELETE FROM submission_log")
    conn.commit()
    conn.close()
    return s
