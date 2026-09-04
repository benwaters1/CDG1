"""The addresses the site used to have.

Twenty-four Squarespace paths are still in Google, in old newsletters and on
other people's blogs. Every one of them has been a 404 since the site moved,
which loses the visit and the search ranking with it. They now answer 301.

Two things here are worth more than "the redirect works".

The first is that a redirect must never take an address a real view already
answers on. Five of the twenty-four are in that position -- /book, /workshops,
/restaurant, /gallery and /events already resolve to the page the list wants.
Registering those would at best do nothing and at worst put a second rule on a
live route, which is a fault this app has had before and which does not
announce itself: both rules are valid, one of them wins, and nothing errors.

The second is that the list comes from a file the design side hands over. A
malformed line in it must cost one redirect and not the site, because the file
is written by hand outside this repository and nobody here reviews it line by
line before it lands.
"""
from _harness import Suite, clients

import io
import os
import tempfile

import _harness

m = _harness.m


def run():
    s = Suite("old addresses")
    anon = m.app.test_client()
    result = m.OLD_PATHS

    s.section("The old addresses answer")

    s.check("the handover file was found and read", bool(m.read_old_paths()),
            detail="REDIRECTS.txt is missing or unreadable — every old link "
                   "is a 404 again and nothing says so")
    s.check("redirects were registered", len(result["added"]) > 0,
            detail=str(len(result["added"])))

    for old, endpoint in result["added"][:6]:
        r = anon.get(old)
        with m.app.test_request_context():
            wants = m.url_for(endpoint)
        s.check("%s reaches %s" % (old, endpoint),
                r.status_code == 301
                and (r.headers.get("Location") or "").endswith(wants),
                detail="HTTP %s -> %s" % (r.status_code, r.headers.get("Location")))

    # 301 and not 302. A search engine moves the ranking across for a permanent
    # redirect and not for a temporary one, and moving the ranking is most of
    # why this exists -- a 302 would send the visitor to the right page and
    # leave the old address holding the rank for ever.
    codes = {anon.get(old).status_code for old, _ in result["added"]}
    s.check("every one is permanent, not temporary", codes == {301},
            detail=str(sorted(codes)))

    s.section("And none of them stole a live address")

    s.check("the ones already answered by a real view were left alone",
            len(result["skipped"]) >= 5,
            detail=str([o for o, _, _ in result["skipped"]]))
    for old, _endpoint, why in result["skipped"]:
        if why != "already a route":
            continue
        r = anon.get(old)
        s.check("%s is still the page itself, not a redirect to it" % old,
                r.status_code == 200,
                detail="HTTP %s — a redirect has taken a live route"
                       % r.status_code)

    # The real thing being guarded: one address, one rule. Both rules would be
    # valid, one would win, and nothing anywhere would error.
    rules = {}
    for rule in m.app.url_map.iter_rules():
        rules.setdefault(rule.rule, []).append(rule.endpoint)
    doubled = {path: eps for path, eps in rules.items()
               if len(eps) > 1 and any(e.startswith("old_path_") for e in eps)}
    s.check("no address has both a redirect and a view on it", not doubled,
            detail=str(list(doubled.items())[:3]))

    s.section("A bad line costs one redirect, not the site")

    # Two layers, and they refuse different things. read_old_paths judges the
    # SHAPE of a line; whether the endpoint it names exists is not knowable
    # from the file, so registration is what refuses that. Checked separately,
    # because a test that lumped them together would pass with either one gone.
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write("# a comment\n"
                     "\n"
                     "/good-one            book_rooms\n"
                     "no-slash             book_rooms\n"
                     "/three  fields  here\n"
                     "/bad-name            not an identifier\n"
                     "/missing-endpoint    no_such_view_anywhere\n"
                     "   \n")
        parsed = m.read_old_paths(path)
        s.check("a good line is read", ("/good-one", "book_rooms") in parsed)
        s.check("one with no leading slash is skipped rather than raising",
                not any(o == "no-slash" for o, _ in parsed))
        s.check("and so is one with the wrong number of fields",
                not any(o == "/three" for o, _ in parsed))
        s.check("three malformed lines, three skips",
                len(parsed) == 2, detail=str(parsed))
        # Well-formed, and still refused -- by the layer that can actually
        # tell. url_for on a name with no route raises BuildError, so without
        # this guard one bad line in a handed-over file is a 500 on that path.
        s.check("a line naming a page that does not exist is well-formed",
                ("/missing-endpoint", "no_such_view_anywhere") in parsed)

        before = len(list(m.app.url_map.iter_rules()))
        # Caught rather than allowed to propagate. If dry_run stops honouring
        # itself, add_url_rule runs after the app has served a request and
        # Flask raises — which WOULD fail the run, but as a crashed suite in a
        # run of 282, with a traceback rather than a sentence. Named instead.
        try:
            asked = m.register_old_paths(entries=parsed, dry_run=True)
            blew_up = ""
        except Exception as exc:                       # noqa: BLE001
            asked = {"added": [], "skipped": []}
            blew_up = str(exc)[:90]
        s.check("asking about a made-up list does not raise", not blew_up,
                detail=blew_up)
        s.check("and is refused when it comes to be wired up",
                any(o == "/missing-endpoint" and why == "no such endpoint"
                    for o, _e, why in asked["skipped"]),
                detail=str(asked["skipped"]))
        s.check("while the good one beside it would still be wired",
                ("/good-one", "book_rooms") in asked["added"],
                detail="one bad line must cost one redirect, not the file")
        s.check("and asking left no route behind",
                len(list(m.app.url_map.iter_rules())) == before,
                detail="a suite running after this one would be measuring an "
                       "app this test invented")
    finally:
        os.remove(path)

    # Also caught rather than propagated. Without the guard this raises at
    # IMPORT, so the site would not boot at all if the handover arrived without
    # the file -- worth a sentence saying exactly that rather than a traceback.
    absent = os.path.join(tempfile.gettempdir(), "zz-no-such-redirects.txt")
    try:
        got, raised = m.read_old_paths(absent), ""
    except Exception as exc:                           # noqa: BLE001
        got, raised = None, type(exc).__name__
    s.check("a file that is not there is not an error either",
            got == [] and not raised,
            detail=raised and "%s — this runs at import, so the site would "
                              "not boot without the file" % raised)

    s.section("The not-found page still works for everything else")

    r = anon.get("/genuinely-nothing-here-zz")
    s.check("an address on no list is still a 404", r.status_code == 404)
    s.check("and still the château's own page, not a bare message",
            len(r.get_data(as_text=True)) > 2000,
            detail="%d bytes" % len(r.get_data(as_text=True)))
    api = anon.get("/api/genuinely-nothing-here-zz")
    s.check("and /api/ still answers a machine in JSON",
            api.status_code == 404 and api.is_json,
            detail="HTTP %s, json=%s" % (api.status_code, api.is_json))

    return s


if __name__ == "__main__":
    print(run().report())
