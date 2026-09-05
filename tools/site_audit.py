"""Walk the public site the way a visitor does, and report what is wrong with it.

    python tools/site_audit.py            # the whole report
    python tools/site_audit.py --brief    # findings only
    python tools/site_audit.py --json     # for something else to read

WHY THIS EXISTS ALONGSIDE THE SUITE.

The suite checks the SOURCE, and it is right to: test_accessibility reads the
templates, test_links resolves every url_for against the url_map, and both
cost nothing and run on every commit. What none of them does is open the site
and look at what a visitor is actually handed.

The difference is not academic. A url_for that resolves can still render a link
to a page that 404s for somebody with no login. A template with an <h1> in it
can render two of them once a partial is included twice. A page can be
perfectly built and reachable from nowhere. Those are all faults of the
rendered site, and the only way to find them is to render it.

So this crawls. It starts at the front door as a stranger with no cookie,
follows every internal link it is given, and measures three things.

  STRUCTURE      what a visitor can reach, how far in it is, what is
                 unreachable, and what has no way onward
  FUNCTIONALITY  what answers, what breaks, and whether the forms and links
                 on the page point anywhere real
  USABILITY      how long the pages are on a phone, whether the pictures are
                 described, whether the fields say what they want, and
                 whether anything can be operated by somebody not using a
                 mouse

IT IS A REPORT, NOT A GATE. It runs against the real database, so its numbers
move with the content, and a number that moves cannot be a pass mark. Anything
it finds that IS worth freezing belongs in tests/ as its own check, and
several already have been.
"""
import argparse
import json
import os
import re
import sys
from collections import deque
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as m                                        # noqa: E402

# Pages a stranger is not meant to reach, and machinery nobody navigates to.
SKIP = re.compile(
    r"^/(admin|management|staff|pos|till|api|static|mirrored-photo|room-photos"
    r"|uploads|logout|login|kitchen|pass|rota|team|hr|payroll|clock|breaks"
    # Staff screens on short paths, which look public and are not.
    r"|announcements|arrive|availability|breakfast|candidates|contacts|directory"
    r"|expenses|handover|leave|manual|mileage|my-|shifts|stock|suppliers|tasks"
    r"|timesheets|training|vehicles|documents|chat|notifications|profile)\b")
# Where a payment provider sends somebody back to. Reached from Stripe and
# never from a link here, so "nothing links to this" is the design rather than
# a fault -- and a report that says otherwise teaches people to skim it.
RETURNED_TO = re.compile(r"^/(book|workshops|events)/(stripe|payment|share)-?")
# Anything carrying somebody's own token: real, private, and not the shopfront.
PRIVATE = re.compile(r"/(confirmation|manage|statement|portal|feedback|pay|share)/")


class Page(HTMLParser):
    """The handful of things about a rendered page this report is about."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.og = set()
        self.headings = []          # (level, text)
        self.links = []             # (href, text)
        self.images = []            # (src, alt_or_None)
        self.forms = []             # (action, method, [field names])
        self.inputs = []            # (type, name, id, autocomplete, has_label)
        self.labels_for = set()
        self._in_label = 0
        self.lang = ""
        self.noindex = False
        self._want = None
        self._buf = []
        self._form = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "title":
            self._want = "title"
            self._buf = []
        elif tag == "meta":
            name = (a.get("name") or "").lower()
            prop = (a.get("property") or "").lower()
            if name == "description":
                self.description = a.get("content", "")
            if name == "robots" and "noindex" in (a.get("content") or "").lower():
                self.noindex = True
            if prop.startswith("og:"):
                self.og.add(prop)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._want = tag
            self._buf = []
        elif tag == "a":
            self._want = "a"
            self._buf = []
            self._href = a.get("href", "")
        elif tag == "img":
            self.images.append((a.get("src", ""), a.get("alt")))
        elif tag == "form":
            self._form = [a.get("action", ""), (a.get("method") or "get").lower(), []]
            self.forms.append(self._form)
        elif tag == "label":
            # Wrapping is a label too. `<label><input> One or two</label>` needs
            # no for/id at all, and reading only the for= form reported forty
            # correctly-labelled radio buttons as unlabelled -- an audit that
            # cries wolf is one nobody reads twice.
            self._in_label += 1
            if a.get("for"):
                self.labels_for.add(a["for"])
        elif tag in ("input", "select", "textarea"):
            if self._form is not None and a.get("name"):
                self._form[2].append(a["name"])
            if (a.get("type") or "").lower() not in ("hidden", "submit", "button"):
                self.inputs.append({
                    "wrapped": self._in_label > 0,
                    "type": (a.get("type") or "text").lower(),
                    "name": a.get("name", ""), "id": a.get("id", ""),
                    "autocomplete": a.get("autocomplete", ""),
                    "aria_label": a.get("aria-label", ""),
                    "placeholder": a.get("placeholder", ""),
                })

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        if tag == "label" and self._in_label:
            self._in_label -= 1
        if self._want != tag and not (self._want == "title" and tag == "title"):
            if self._want == "a" and tag == "a":
                pass
            else:
                return
        text = " ".join("".join(self._buf).split())
        if self._want == "title":
            self.title = text
        elif self._want == "a":
            self.links.append((getattr(self, "_href", ""), text))
        elif self._want and self._want.startswith("h"):
            self.headings.append((int(self._want[1]), text))
        self._want = None
        self._buf = []

    def handle_data(self, data):
        if self._want:
            self._buf.append(data)


def read(html):
    p = Page()
    p.feed(html)
    return p


def public_paths():
    """Every parameterless page a stranger is meant to be able to open.

    A staff page is one the app itself puts in an access area -- ENDPOINT_AREA
    is what the permission system reads, so it is the same answer the app gives
    when it decides whether to let somebody in. Guessing from the path was a
    regex that grew every time this ran and was wrong the moment somebody added
    a staff page on a short address.
    """
    staff = getattr(m, "ENDPOINT_AREA", {})
    out = []
    for rule in m.app.url_map.iter_rules():
        path = str(rule)
        if "GET" not in rule.methods or rule.arguments:
            continue
        if SKIP.match(path) or path.startswith("/static"):
            continue
        if rule.endpoint in staff:
            continue
        out.append(path)
    return sorted(set(out))


def crawl(client, start="/"):
    """Follow the site's own links, breadth first, recording how deep each is."""
    seen, depth, order = {}, {start: 0}, deque([start])
    while order:
        path = order.popleft()
        if path in seen:
            continue
        try:
            r = client.get(path, follow_redirects=False)
        except Exception as exc:                       # noqa: BLE001
            seen[path] = {"status": "CRASH", "error": str(exc)[:120], "page": None}
            continue
        if r.status_code in (301, 302, 303, 307, 308):
            target = (r.headers.get("Location") or "").split("?")[0]
            seen[path] = {"status": r.status_code, "redirects_to": target, "page": None}
            if target.startswith("/") and not SKIP.match(target) and target not in depth:
                depth[target] = depth[path]
                order.append(target)
            continue
        body = r.get_data(as_text=True)
        page = read(body) if r.content_type.startswith("text/html") else None
        seen[path] = {"status": r.status_code, "page": page, "bytes": len(body)}
        if page is None:
            continue
        for href, _text in page.links:
            href = (href or "").split("#")[0].split("?")[0]
            if not href.startswith("/") or SKIP.match(href) or PRIVATE.search(href):
                continue
            if href not in depth:
                depth[href] = depth[path] + 1
                order.append(href)
    return seen, depth


def audit():
    client = m.app.test_client()          # no cookie: a stranger, like a visitor
    seen, depth = crawl(client)
    declared = public_paths()

    findings = {"structure": [], "functionality": [], "usability": []}

    def note(where, severity, what, detail=""):
        findings[where].append({"severity": severity, "what": what, "detail": detail})

    # ---------------------------------------------------------- STRUCTURE
    reached = {p for p, r in seen.items() if r.get("page") is not None}
    # The old Squarespace addresses are SUPPOSED to be unlinked: they exist for
    # inbound links in Google and in newsletters people kept, and adding them to
    # the site would be advertising addresses the house has moved on from. They
    # were 89 of the 89 "nothing links to this", which is the kind of finding
    # that teaches somebody to stop reading the report.
    legacy = {p for p, _e, _perm in getattr(m, "LEGACY_PATHS", [])}
    unreachable = [p for p in declared if p not in seen and p not in legacy
                   and not RETURNED_TO.match(p)]
    if unreachable:
        note("structure", "warn",
             "%d public page(s) nothing links to" % len(unreachable),
             ", ".join(unreachable[:8]))

    deep = sorted(((depth.get(p, 0), p) for p in reached if depth.get(p, 0) >= 3),
                  reverse=True)
    if deep:
        note("structure", "note",
             "%d page(s) are three or more clicks from the front door"
             % len(deep), ", ".join("%s (%d)" % (p, d) for d, p in deep[:6]))

    # A page a visitor can arrive at and do nothing from is a dead end.
    dead_ends = []
    for p in sorted(reached):
        page = seen[p]["page"]
        onward = {h.split("#")[0].split("?")[0] for h, _ in page.links
                  if h.startswith("/") and h.split("#")[0].split("?")[0] != p}
        if len(onward) <= 1:
            dead_ends.append(p)
    if dead_ends:
        note("structure", "warn", "%d page(s) offer nowhere to go next"
             % len(dead_ends), ", ".join(dead_ends[:6]))

    titles, descriptions = {}, {}
    for p in sorted(reached):
        page = seen[p]["page"]
        if not page.title:
            note("structure", "blocker", "no <title>", p)
        else:
            titles.setdefault(page.title, []).append(p)
        if not page.description:
            note("structure", "warn", "no meta description", p)
        else:
            descriptions.setdefault(page.description, []).append(p)
        h1s = [t for lvl, t in page.headings if lvl == 1]
        if not h1s:
            note("structure", "warn", "no <h1>", p)
        elif len(h1s) > 1:
            note("structure", "warn", "%d <h1> headings" % len(h1s),
                 "%s — %s" % (p, " / ".join(h1s[:3])))
        levels = [lvl for lvl, _ in page.headings]
        for a, b in zip(levels, levels[1:]):
            if b > a + 1:
                note("structure", "note", "heading level jumps h%d to h%d" % (a, b), p)
                break
        missing_og = {"og:title", "og:description", "og:image"} - page.og
        if missing_og and not page.noindex:
            note("structure", "note", "no %s" % ", ".join(sorted(missing_og)), p)

    for title, pages in titles.items():
        if len(pages) > 1:
            note("structure", "warn", "%d pages share one <title>" % len(pages),
                 "%r on %s" % (title[:40], ", ".join(pages[:4])))
    for desc, pages in descriptions.items():
        if len(pages) > 1:
            note("structure", "note",
                 "%d pages share one meta description" % len(pages),
                 ", ".join(pages[:4]))

    # ------------------------------------------------------ FUNCTIONALITY
    for p in sorted(seen):
        r = seen[p]
        if r["status"] == "CRASH":
            note("functionality", "blocker", "the page raised", "%s — %s" % (p, r["error"]))
        elif isinstance(r["status"], int) and r["status"] >= 500:
            note("functionality", "blocker", "HTTP %s" % r["status"], p)
        elif isinstance(r["status"], int) and r["status"] == 404:
            note("functionality", "blocker", "linked from the site and 404s", p)

    live = {str(rule) for rule in m.app.url_map.iter_rules()}
    for p in sorted(reached):
        page = seen[p]["page"]
        for action, method, fields in page.forms:
            target = (action or p).split("?")[0]
            if target.startswith("/") and not m.app.url_map.bind("x").test(
                    target, "POST" if method == "post" else "GET"):
                note("functionality", "blocker",
                     "a form posts somewhere with no route",
                     "%s -> %s" % (p, target))
            if method == "post" and "csrf_token" not in fields:
                note("functionality", "warn", "a form with no csrf token",
                     "%s -> %s" % (p, target))

    # --------------------------------------------------------- USABILITY
    PHONE = 812                       # one screen on the phone they measured
    for p in sorted(reached):
        page, size = seen[p]["page"], seen[p]["bytes"]
        undescribed = [s for s, alt in page.images if alt is None]
        if undescribed:
            note("usability", "warn", "%d image(s) with no alt attribute"
                 % len(undescribed), p)
        empty_alt = [s for s, alt in page.images if alt == ""]
        if len(empty_alt) > 3:
            note("usability", "note", "%d image(s) with an empty alt" % len(empty_alt),
                 "%s — decorative is fine, but this many is usually an oversight" % p)
        for f in page.inputs:
            named = (f["wrapped"] or f["aria_label"]
                     or (f["id"] and f["id"] in page.labels_for))
            if not named:
                note("usability", "warn", "a field with no label",
                     "%s — %s" % (p, f["name"] or f["type"]))
            if f["name"] in ("email", "guest_email", "contact_email") \
                    and f["type"] != "email":
                note("usability", "note", "an email field typed as %r" % f["type"],
                     "%s — a phone shows the wrong keyboard" % p)
            if "phone" in (f["name"] or "") and f["type"] not in ("tel",):
                note("usability", "note", "a telephone field typed as %r" % f["type"], p)
            if f["name"] in ("guest_name", "contact_name", "guest_email",
                             "contact_email", "guest_phone", "contact_phone") \
                    and not f["autocomplete"]:
                note("usability", "note", "no autocomplete on %r" % f["name"],
                     "%s — the guest types their own name again" % p)
        vague = [t for h, t in page.links
                 if t.strip().lower() in ("click here", "here", "read more", "more",
                                          "link", "this")]
        if vague:
            note("usability", "note", "%d link(s) whose text says nothing"
                 % len(vague), "%s — %s" % (p, ", ".join(sorted(set(vague))[:4])))
        by_text = {}
        for href, text in page.links:
            key = text.strip().lower()
            if key and href.startswith("/"):
                by_text.setdefault(key, set()).add(href.split("#")[0])
        # Siblings are fine: "Book in" on each of five room cards goes to five
        # rooms, and the card it sits in is what names it. One word for two
        # unrelated parts of the site is not. Judged on the first path segment,
        # the same rule test_site_audit settled on.
        clashing = {t: h for t, h in by_text.items()
                    if len({x.strip("/").split("/")[0] for x in h}) > 1}
        if clashing:
            first = list(clashing)[0]
            note("usability", "note",
                 "%d link text(s) point at more than one place" % len(clashing),
                 "%s — %r goes to %s" % (p, first[:30],
                                         ", ".join(sorted(clashing[first])[:3])))
        if size > 250_000:
            note("usability", "note", "%d KB of html" % (size // 1024),
                 "%s — long pages were measured at 42 screens on a phone" % p)
    return seen, depth, findings


SEV = {"blocker": 0, "warn": 1, "note": 2}


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    seen, depth, findings = audit()
    reached = [p for p, r in seen.items() if r.get("page") is not None]

    if args.json:
        print(json.dumps({"reached": sorted(reached), "findings": findings}, indent=2))
        return 0

    if not args.brief:
        print("THE PUBLIC SITE, WALKED AS A STRANGER")
        print("=" * 74)
        print("%d page(s) reached from the front door, %d public route(s) declared"
              % (len(reached), len(public_paths())))
        by_depth = {}
        for p in reached:
            by_depth.setdefault(depth.get(p, 0), []).append(p)
        for d in sorted(by_depth):
            print("  %d click%s: %s" % (d, "" if d == 1 else "s",
                                        ", ".join(sorted(by_depth[d])[:9])))
        print()

    total = 0
    for area in ("structure", "functionality", "usability"):
        rows = sorted(findings[area], key=lambda f: SEV.get(f["severity"], 9))
        total += len(rows)
        print("%s — %d finding(s)" % (area.upper(), len(rows)))
        print("-" * 74)
        if not rows:
            print("  nothing")
        for f in rows[:40]:
            print("  %-8s %s" % (f["severity"], f["what"]))
            if f["detail"]:
                print("           %s" % f["detail"][:150])
        if len(rows) > 40:
            print("  ... and %d more" % (len(rows) - 40))
        print()

    blockers = sum(1 for a in findings.values() for f in a if f["severity"] == "blocker")
    print("%d finding(s), %d of them blocking." % (total, blockers))
    return 1 if blockers else 0


if __name__ == "__main__":
    sys.exit(main())
