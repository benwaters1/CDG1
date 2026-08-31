"""The cache that kept ten design handovers off the iPad.

The service worker precaches the app shell — both stylesheets, the icons, the
offline page — and its activate handler deletes every cache whose name is not
the current one. So the cache NAME is the only thing that can clear an
installed shell.

It was a constant. `var CACHE = 'gudanes-shell-v1'`, with a comment saying to
bump it by hand when the precached list changed, and it stayed at v1 through
every deploy since. A phone or an iPad with the app on its home screen was
therefore serving the CSS from whenever it first installed. Nothing errored and
nothing looked broken; the pages simply looked like last month, on exactly the
devices the house actually uses, while everybody else saw the new design.

THE CHECK THAT MATTERS is the second section: the name has to CHANGE when a
precached file changes. A version that is merely present proves nothing — v1
was present. It is derived from the contents of sw.js and of every /static/
file sw.js lists, so it cannot be forgotten.

The list is read out of sw.js rather than repeated in Python, because two lists
drift and the drift is silent. The last section holds that together.
"""
import io
import os
import re

from _harness import Suite, clients
import _harness

m = _harness.m
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def _worker(client):
    r = client.get("/sw.js")
    return r, r.get_data(as_text=True)


def _name(body):
    found = re.search(r"gudanes-shell-[A-Za-z0-9]+", body)
    return found.group(0) if found else None


def run():
    s = Suite("Shell cache")
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()

    s.section("The worker is served, and from the root")
    r, body = _worker(anon)
    s.check("it is public — a logged-out phone still needs the offline page",
            r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("as JavaScript", "javascript" in (r.headers.get("Content-Type") or ""),
            detail=f"{r.headers.get('Content-Type')}")
    s.check("from the site root, so its scope is the whole app",
            m.app.url_map.bind("localhost").match("/sw.js")[0] == "service_worker",
            detail="at /static/sw.js it can only control /static/")

    s.section("The cache name is not a constant any more")
    name = _name(body)
    s.check("it carries a version", bool(name), detail=f"{body[:120]!r}")
    s.check("and it is not the hand-bumped v1 that never moved",
            name != "gudanes-shell-v1",
            detail=f"{name} — a constant is the whole bug: activate only clears "
                   "caches whose name differs, so a shell installed once was "
                   "never replaced")

    s.section("It CHANGES when a precached file changes")
    # The claim this file exists to prove. A version merely being present is
    # what v1 was.
    css = os.path.join(STATIC, "gudanes.css")
    original = io.open(css, "r", encoding="utf-8", newline="").read()
    before = name
    try:
        io.open(css, "w", encoding="utf-8", newline="").write(
            original + "\n/* zz shell cache probe */\n")
        m._SHELL_VERSION = None          # a deploy restarts the process
        _r2, body2 = _worker(anon)
        after = _name(body2)
    finally:
        io.open(css, "w", encoding="utf-8", newline="").write(original)
        m._SHELL_VERSION = None
    s.check("a changed stylesheet gives a different cache name", before != after,
            detail=f"{before} -> {after} — the shell would keep the old CSS")
    _r3, body3 = _worker(anon)
    s.check("and putting it back gives the original name again",
            _name(body3) == before, detail=f"{_name(body3)} vs {before}")

    s.section("The worker itself is never cached")
    # The browser re-fetches sw.js to notice a new worker at all. A cached one
    # is a shell that can never be replaced, whatever the name says.
    control = (r.headers.get("Cache-Control") or "").lower()
    s.check("it says no-store", "no-store" in control or "no-cache" in control,
            detail=f"{control!r}")

    s.section("One list, not two")
    # The version is computed from the files sw.js names. If Python kept its own
    # copy of that list the two would drift, and the drift would be silent.
    source = io.open(os.path.join(STATIC, "sw.js"), encoding="utf-8").read()
    listed = re.findall(r"'/static/([^']+)'", source)
    s.check("sw.js still lists its shell where the version can read it",
            len(listed) >= 3, detail=f"{listed}")
    s.check("and both stylesheets are in it",
            any(f.endswith("style.css") for f in listed)
            and any(f.endswith("gudanes.css") for f in listed),
            detail=f"{listed} — a stylesheet outside the shell is one the "
                   "version cannot notice")
    missing = [f for f in listed if not os.path.exists(os.path.join(STATIC, f))]
    s.check("and every file it lists exists",
            not missing, detail=f"missing: {missing} — install adds them one at "
                                "a time and swallows failures, so a typo here "
                                "costs that file silently")

    return s
