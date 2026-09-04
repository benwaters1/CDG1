"""The addresses the site had before, which people are still following.

The château was on Squarespace. Those paths are the ones in Google, in
newsletters people kept, and on other people's blogs, and every one of them
has answered 404 since the rebuild. A 404 on a link somebody followed on
purpose is a guest who went looking for the rooms and did not arrive.

WHAT THIS IS REALLY GUARDING, because "does /rooms redirect" is the easy half.

  IT MUST NEVER SHADOW A REAL PAGE. Five of the old paths ARE the page now --
  /book, /workshops, /restaurant, /gallery and /events. A redirect on one of
  those would send a guest away from the page they asked for, and the site
  would still answer 200 the whole way. The wiring skips a taken path rather
  than raising, because it runs at import and a hard failure there takes the
  house's website down over a redirect. So the loud failure is here, where it
  costs nothing.

  IT MUST LAND SOMEWHERE THAT ANSWERS. A redirect to a route that has since
  been renamed is a 404 with an extra step, and it is invisible: the redirect
  itself still returns a perfectly good 301.

  AND PERMANENT MEANS PERMANENT. A 301 is cached by browsers more or less
  forever, which is what makes it worth doing -- it hands the old page's
  search ranking over -- and also what makes it hard to take back. The three
  the house might one day want for itself go out as 302s. On a house that has
  kept a Captain's Log for a decade, /journal is not a safe thing to give away
  permanently.
"""
from _harness import Suite, clients

import _harness

m = _harness.m


def run():
    s = Suite("The addresses people still have")
    _oc, _ec, _owner, _emp = clients()
    c = m.app.test_client()
    rules = {str(r.rule): r.endpoint for r in m.app.url_map.iter_rules()}

    s.section("An old link arrives somewhere")
    s.check("there are old paths wired at all", len(m.LEGACY_WIRED) >= 15,
            detail=str(len(m.LEGACY_WIRED)))
    landed, missed = [], []
    for path, endpoint, _permanent in m.LEGACY_PATHS:
        if path not in m.LEGACY_WIRED:
            continue
        r = c.get(path)
        (landed if r.status_code in (301, 302) else missed).append(path)
    s.check("and every one of them redirects", not missed, detail=str(missed))
    s.check("/rooms goes to the rooms",
            (c.get("/rooms").headers.get("Location") or "").endswith(
                m.app.url_map.bind("x").build("book_rooms")),
            detail=str(c.get("/rooms").headers.get("Location")))

    s.section("It never sends anybody away from a page that exists")
    # The five that ARE the page. A redirect on one of these would be a guest
    # asking for the workshops and being sent somewhere else, with a 200 at
    # the end of it, which is why nothing would ever notice.
    shadowed = [p for p, _e, _perm in m.LEGACY_PATHS if p in rules
                and not rules[p].startswith("legacy_")]
    s.check("no old path is listed over a real route", not shadowed,
            detail=f"{shadowed} — these are the pages themselves, and a "
                   "redirect on one answers 200 all the way to the wrong page")
    for path in ("/workshops", "/gallery", "/events", "/restaurant", "/book"):
        s.check(f"{path} still serves its own page",
                c.get(path).status_code == 200,
                detail=str(c.get(path).status_code))

    s.section("And it lands on something that answers")
    dead = []
    for path, endpoint, _permanent in m.LEGACY_PATHS:
        if path not in m.LEGACY_WIRED:
            continue
        if endpoint not in {r.endpoint for r in m.app.url_map.iter_rules()}:
            dead.append((path, endpoint))
            continue
        if c.get(path, follow_redirects=True).status_code != 200:
            dead.append((path, endpoint))
    s.check("every redirect ends on a page that renders", not dead,
            detail=f"{dead} — a redirect to a renamed route is a 404 with "
                   "an extra step, and the 301 itself still looks perfect")

    s.section("Permanent where it earns it, temporary where it does not")
    perm = {p: c.get(p).status_code for p, _e, want in m.LEGACY_PATHS
            if want and p in m.LEGACY_WIRED}
    s.check("the settled ones are permanent, so the ranking carries over",
            set(perm.values()) == {301}, detail=str(perm))
    temp = {p: c.get(p).status_code for p, _e, want in m.LEGACY_PATHS
            if not want and p in m.LEGACY_WIRED}
    s.check("and the ones the house may want back are not",
            set(temp.values()) == {302},
            detail=f"{temp} — a 301 sits in every returning reader's "
                   "browser cache; this house has kept a Captain's Log for a "
                   "decade and may yet publish it at its own address")
    s.check("/journal is one of them", "/journal" in temp, detail=str(list(temp)))

    s.section("The tail of the link comes with it")
    r = c.get("/rooms?utm_source=newsletter&utm_campaign=spring")
    s.check("a campaign tag survives the redirect",
            "utm_source=newsletter" in (r.headers.get("Location") or ""),
            detail=f"{r.headers.get('Location')} — dropping it turns every "
                   "arrival from a campaign the house paid for into an "
                   "anonymous one")
    s.check("and so does the rest of it",
            "utm_campaign=spring" in (r.headers.get("Location") or ""),
            detail=str(r.headers.get("Location")))
    # There was a check here that a link with no query string does not come
    # back as "/book?". It is gone because it cannot fail: Werkzeug strips an
    # empty query off the Location header, so the code could append a bare
    # question mark to every redirect and the check would still pass. A check
    # that cannot fail reads as cover, which is worse than not having one.

    s.section("An address nobody ever had still says so")
    s.check("a made-up path is still a 404",
            c.get("/definitely-not-a-page").status_code == 404,
            detail="a catch-all that redirects everything would hide every "
                   "broken link the site has")

    return s


if __name__ == "__main__":
    print(run().report())
