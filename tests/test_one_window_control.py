"""How far a page looks is chosen one way on every page.

WHAT WENT WRONG. Seventeen pages each drew their own row of "30 days / 90 days"
links. They marked the chosen one four different ways -- swapping btn-mini for
btn-ghost, adding btn-primary, adding btn-mini-active, or one of those the
other way round -- wrote it as "30 days", "30d" or "3m", said nothing about
whether the page was looking back or ahead, and most of them sat in the page
head among the links to other pages, looking exactly like them.

WHAT THIS PINS, on each page as the owner gets it:
  - one control, _window_choice.html, under the title rather than in it;
  - it says "The last" or "The next";
  - the choice in force is marked, for a screen reader too;
  - every choice opens, and is then the one marked.
And no template draws a loop of window links of its own again.
"""
import glob
import os
import re

from _harness import Suite, clients
import _harness

m = _harness.m
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# endpoint: (param, the default the route uses, looks ahead)
PAGES = {
    "contracted_hours_page": ("weeks", 4, False),
    "cover_gaps_page": ("days", 28, True),
    "discount_cost_page": ("months", 12, False),
    "kitchen_service_times": ("days", 30, False),
    "kitchen_waste": ("days", 90, False),
    "management_night_cost": ("months", 3, False),
    "money_ahead_page": ("days", 90, True),
    "overtime_page": ("weeks", 12, False),
    "demand_report": ("days", 90, False),
    "restaurant_covers": ("days", 21, True),
    "restaurant_what_sells": ("days", 90, False),
    "room_checks_page": ("days", 2, True),
    "room_economics_page": ("months", 12, False),
    "rota_clashes_page": ("days", 28, True),
    "rota_vs_clock_page": ("days", 30, False),
    "spend_by_vendor_page": ("months", 12, False),
}

BAR = re.compile(r'<div class="window-bar">(.*?)</nav>\s*</div>', re.S)
CHOICE = re.compile(r'<a href="([^"]+)"\s*class="([^"]*)"([^>]*)>\s*([^<]+?)\s*</a>')
# A loop of links that sets a window, drawn by hand.
HAND_LOOP = re.compile(r"\{%\s*for n in \[[\d,\s]+\]\s*%\}\s*<a href=\"\{\{\s*url_for\("
                       r"'\w+',\s*(?:days|weeks|months)=n\)")


def _bar(page):
    bars = BAR.findall(page)
    return bars, [dict(href=h.replace("&amp;", "&"), on="is-active" in c,
                       current='aria-current="true"' in rest, words=w)
                  for h, c, rest, w in CHOICE.findall(bars[0])] if bars else []


def _head(page):
    """The page head, to the </div> that closes it."""
    a = page.find('<div class="page-head">')
    if a < 0:
        return ""
    depth = 0
    for m_ in re.finditer(r"<div\b|</div>", page[a:]):
        depth += 1 if m_.group(0) == "<div" else -1
        if depth == 0:
            return page[a:a + m_.end()]
    return page[a:]


def run():
    s = Suite("how far a page looks, chosen one way")
    oc, ec, _o, _e = clients()
    with m.app.test_request_context():
        urls = {ep: m.url_for(ep) for ep in PAGES}

    s.section("Each page draws the one control, and draws it the same way")
    for ep, (param, default, ahead) in PAGES.items():
        r = oc.get(urls[ep])
        page = r.get_data(as_text=True)
        bars, choices = _bar(page)
        s.check(f"{ep}: draws, with one control", r.status_code == 200 and len(bars) == 1,
                detail=f"HTTP {r.status_code}, {len(bars)} controls")
        if not choices:
            continue
        words = "The next" if ahead else "The last"
        s.check(f"{ep}: says {words!r}",
                words in bars[0] or (ep == "room_checks_page" and "Arriving" in bars[0]))
        s.check(f"{ep}: the default is the one marked, for a screen reader too",
                [c["words"] for c in choices if c["on"] and c["current"]]
                == [c["words"] for c in choices if f"{param}={default}" in c["href"]]
                and sum(c["on"] for c in choices) == 1,
                detail=str([(c["words"], c["on"], c["current"]) for c in choices]))
        s.check(f"{ep}: not among the links in the page head",
                "window-bar" not in _head(page))
        # Every choice opens, and is then the one in force.
        wrong = []
        for c in choices:
            got = oc.get(c["href"])
            _b, after = _bar(got.get_data(as_text=True))
            marked = [x["words"] for x in after if x["on"]]
            if got.status_code != 200 or marked != [c["words"]]:
                wrong.append(f"{c['words']}: HTTP {got.status_code}, marked {marked}")
        s.check(f"{ep}: every choice opens and is then the one marked", not wrong,
                detail="; ".join(wrong[:3]))

    s.section("Nobody draws their own again")
    hand = []
    for f in sorted(glob.glob(os.path.join(ROOT, "templates", "*.html"))):
        if HAND_LOOP.search(open(f, encoding="utf-8").read()):
            hand.append(os.path.basename(f))
    s.check("no template has a loop of window links of its own", not hand,
            detail=", ".join(hand))
    s.check("the scan can see one",
            bool(HAND_LOOP.search("{% for n in [7, 30] %}\n<a href=\"{{ url_for('x', days=n) }}\"")))

    s.section("An employee sees none of it")
    for ep in ("money_ahead_page", "spend_by_vendor_page", "overtime_page"):
        s.check(f"{ep} is not theirs", ec.get(urls[ep]).status_code in (302, 403))
    return s


if __name__ == "__main__":
    print(run().report())
