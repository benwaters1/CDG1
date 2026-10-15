"""A version on every stylesheet and script address.

/static/gudanes.css was the same address before and after every handover, so
a returning visitor's browser could keep last week's styling over this week's
pages (the design side, 15 October 2026). url_for now adds ?v= and ten
characters of the file's SHA-256, so the address changes when the file does
and only then. It is added by the app, not typed into public_base.html, which
arrives whole in every handover.
"""
import hashlib
import os
import re

from _harness import Suite, clients, ROOT
import _harness

m = _harness.m


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:10]


def run():
    s = Suite("Stylesheets carry their version")
    oc, _ec, _o, _e = clients()
    c = m.app.test_client()

    s.section("The public site and the staff app both carry it")
    want = _sha(os.path.join(ROOT, "static", "gudanes.css"))
    page = c.get("/story").get_data(as_text=True)
    s.check("the public stylesheet is linked with its fingerprint",
            f'/static/gudanes.css?v={want}"' in page,
            detail=str(re.findall(r'href="(/static/gudanes\.css[^"]*)"', page)))
    staff = oc.get("/today").get_data(as_text=True)
    want_staff = _sha(os.path.join(ROOT, "static", "style.css"))
    s.check("and so is the staff one",
            f'/static/style.css?v={want_staff}"' in staff,
            detail=str(re.findall(r'href="(/static/style\.css[^"]*)"', staff)))
    s.check("and the stamped address serves the stylesheet",
            c.get(f"/static/gudanes.css?v={want}").status_code == 200)

    s.section("It changes when the file does, and only then")
    name = "zz_stamp_probe.css"
    path = os.path.join(m.app.static_folder, name)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(".a{color:red}")
        with m.app.test_request_context("/"):
            one = m.url_for("static", filename=name)
            again = m.url_for("static", filename=name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(".a{color:blue}")
        os.utime(path, ns=(os.stat(path).st_atime_ns, os.stat(path).st_mtime_ns + 10**9))
        with m.app.test_request_context("/"):
            two = m.url_for("static", filename=name)
    finally:
        os.remove(path)
        m._STATIC_STAMPS.pop(name, None)
    s.check("the same file gives the same address", one == again and "?v=" in one,
            detail=f"{one} / {again}")
    s.check("a changed file gives a new one", one != two, detail=f"{one} -> {two}")

    s.section("Photographs are left as they are")
    with m.app.test_request_context("/"):
        photo = m.url_for("static", filename="img/found/great-room.jpg")
    s.check("no fingerprint on a photograph", "?v=" not in photo, detail=photo)

    s.section("Offline, an older copy still styles the page")
    sw = open(os.path.join(ROOT, "static", "sw.js"), encoding="utf-8").read()
    s.check("the service worker falls back to a copy saved under another version",
            "ignoreSearch: true" in sw,
            detail="without it, a page cached under one version and a stylesheet "
                   "under another renders unstyled offline")
    return s


if __name__ == "__main__":
    print(run().report())
