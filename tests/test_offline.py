"""What staff see when the château's internet is not there.

The app is installed to a phone home screen and runs standalone — no address
bar, no back button. A failed navigation there is not a browser error page
somebody can retry from, it is a dead screen with no way out, and the internet
at the château is genuinely unreliable.

The service worker itself is JavaScript and cannot be exercised from Python.
What is checkable, and what actually breaks in practice, is everything it
depends on: the offline page existing and being reachable, being self-contained
so it renders when nothing else loads, and the worker still routing writes
straight to the network rather than caching or swallowing them.
"""
import os
import re

from _harness import Suite, ROOT
import io
import _harness

m = _harness.m
SW = os.path.join(ROOT, "static", "sw.js")
OFFLINE = os.path.join(ROOT, "static", "offline.html")


def run():
    s = Suite("Offline")
    sw = open(SW, encoding="utf-8").read()
    page = open(OFFLINE, encoding="utf-8").read()
    # Comments in this worker discuss the very APIs being checked for — the
    # note explaining why addAll is avoided contains the word addAll. Checks
    # about what the code does read this, not the prose around it.
    sw_code = re.sub(r"//[^\n]*", "", sw)

    s.section("The offline page exists and is served")
    s.check("the file is there", os.path.exists(OFFLINE))
    client = m.app.test_client()
    r = client.get("/static/offline.html")
    s.check("and Flask serves it, so the worker can cache it",
            r.status_code == 200, detail=f"HTTP {r.status_code}")

    s.section("It stands on its own")
    # It is shown precisely when the network is not working, so anything it
    # fetches at render time is something it will not get.
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', page)
    s.check("it pulls in nothing over the network", not external,
            detail=", ".join(external[:3]))
    s.check("its styling is inline, so it cannot arrive unstyled",
            "<style>" in page and 'rel="stylesheet"' not in page)

    s.section("It does not tell a comforting lie")
    # The dangerous version of this screen is the reassuring one: somebody who
    # believes they clocked in and did not has a payroll problem that surfaces
    # at the end of the month.
    s.check("it says plainly that nothing was saved",
            "not saved" in page.lower(), detail="no such warning on the page")
    s.check("and names clocking in specifically",
            "clocking in" in page.lower() or "clock" in page.lower())
    s.check("it offers a way to retry", 'id="retry"' in page)

    s.section("Staff who don't read English are not left out")
    for lang, word in (("fr", "connexion"), ("es", "conexión")):
        s.check(f"it carries {lang}", word in page, detail=f"{word!r} missing")

    s.section("The worker caches the shell and nothing else")
    s.check("the offline page is precached", "/static/offline.html" in sw_code)
    s.check("the stylesheets are too, so it renders styled",
            "/static/style.css" in sw_code and "/static/gudanes.css" in sw_code)
    # The original worker cached nothing at all, on the sound reasoning that
    # stale tasks and stale guest data are worse than an honest error. That
    # reasoning still holds for everything except the shell.
    s.check("only /static/ is cached — never a page or an API response",
            "/static/" in sw_code and "'/today'" not in sw_code and '"/today"' not in sw_code)

    s.section("Writes are never cached or swallowed")
    # Clocking in, completing a task, posting to chat. A cached or silently
    # dropped write is the failure this whole screen exists to prevent.
    s.check("non-GET requests are passed through untouched",
            "request.method !== 'GET'" in sw_code, detail="no method guard in the worker")

    s.section("An old shell cannot linger after a deploy")
    s.check("there is a named cache version", "gudanes-shell-v" in sw_code)
    s.check("and activate clears every other cache",
            "caches.delete" in sw_code and "caches.keys" in sw_code)

    s.section("A missing file cannot cost the whole safety net")
    # cache.addAll rejects the entire install if one entry 404s, which would
    # leave no offline page at all — the one thing that must survive.
    s.check("the shell is added file by file, not with addAll",
            "addAll" not in sw_code and "cache.add(" in sw_code)

    s.section("Push still works")
    # The worker was rewritten around it; these are the two handlers the
    # notifications depend on.
    for handler in ("push", "notificationclick"):
        s.check(f"the {handler} handler survived the rewrite",
                f"addEventListener('{handler}'" in sw_code)

    s.section("It is registered by the staff shell")
    base = open(os.path.join(ROOT, "templates", "base.html"), encoding="utf-8").read()
    s.check("base.html registers the worker", "serviceWorker.register" in base)
    s.check("and the manifest is linked, so it installs as an app",
            "manifest.json" in base)

    s.section("Installed, it is usable on the device it is installed on")
    # The same app is a phone in an apron pocket and a till on an iPad by the
    # door. The manifest asked for portrait-primary, which is right for the
    # first and wrong for the second: an installed iPad app would rotate away
    # from the landscape a floor of table tiles is actually read in.
    import json as _json, os as _os
    manifest = _json.loads(io.open(
        _os.path.join(_harness.ROOT, "static", "manifest.json"), encoding="utf-8").read())
    s.check("it installs as a standalone app, not a browser tab",
            manifest.get("display") == "standalone",
            detail=f"display is {manifest.get('display')!r}")
    s.check("and it is not locked to portrait",
            manifest.get("orientation") not in ("portrait", "portrait-primary",
                                                "portrait-secondary"),
            detail=f"orientation {manifest.get('orientation')!r} — the till on an "
                   "iPad cannot be used in landscape")
    s.check("it has an icon to install with",
            bool(manifest.get("icons")), detail="no icons in the manifest")
    s.check("and Safari is told it can go full screen",
            'apple-mobile-web-app-capable" content="yes"' in base,
            detail="added to the home screen it still opens with browser chrome")

    s.section("It is served from the root, or it controls nothing")
    # Found in a browser: a worker's scope is the directory it is served from,
    # so at /static/sw.js it was scoped to /static/ and never saw /today or
    # /arrive. Everything above it was inert — the offline page could not
    # appear on any page a staff member actually opens.
    r = client.get("/sw.js")
    s.check("/sw.js is served", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("as JavaScript", "javascript" in r.headers.get("Content-Type", ""),
            detail=r.headers.get("Content-Type"))
    s.check("and it is the same worker, not a stub",
            b"gudanes-shell-v" in r.data)
    s.check("the page registers that path, not the /static/ one",
            "register('/sw.js')" in base,
            detail="still registering from /static/, which cannot control the app")
    # Phones that installed the app before the fix still hold the old
    # registration, which keeps its push subscription — two workers would
    # race to show the same notification.
    s.check("the old /static/ registration is retired on next load",
            "unregister()" in base and "'/static/'" in base,
            detail="an already-installed phone would keep the broken worker")

    return s
