"""The tops of the pages, as the 23 Nov 2026 audit of the live site found them.

THE EVENT PAGES. Weddings, private events and photoshoots open on a navy hero
with drawn art (g-hero--art) rather than a photograph. The owner's second
review made every filled button navy, in gudanes-pictures.css, which is read
after gudanes.css -- so it beat the hero's own gilt rule at the same weight,
and "Ask about a date" became a navy button on navy. Their words sat in a bare
.g-wrap with nothing under it, where a photograph hero's sit in .g-hero__in
with 64px below, so the button was cut by the hero's bottom edge. And on a
phone every hero is 88svh, there for a photograph: on navy art that was a
screen of empty blue with the button under the sticky bar. All of it rendered
and none of it errored; found on the live site in the 23 Nov audit.

WHAT IS CHECKED. The three pages still open on the art hero with a filled
button; the stylesheet gives that button a light fill with navy words, from a
rule that comes after the navy one it has to beat; the words have room below;
and on a phone the hero is the height of its words with the art up top.

/HISTORY. Its hero is an old postcard with its own printed title along the
top; cropped at the middle, the title sat half-cut under the header's logo on
a wide screen. Its dates, 1213 · 1741 · 2013 (on the home page's hero too),
were the one line with no shadow behind them, a mid gold on grey.

THE CURRENCY CONTROL takes the header's colour, navy, and stayed navy over a
hero photograph when the logo, the menu and the languages turn white.

Each was measured by hand in Chrome on the live pages with the rules injected;
these check the rules are there, and win.
"""
import os
import re

from _harness import Suite
import _harness

m = _harness.m
PAGES = ()  # all three open on a photograph since 23 Nov 2026 (below); the art hero's rules are still held
# 23 Nov 2026, later (owner: "this is really not good"): /events/photoshoots opens on a photograph now, the
# a plate from the kitchen, in a photograph hero's own frame (.g-hero__in), so none of the navy art's faults can reach it.
# Weddings and private events followed the same evening ("i need the wedding and the workshop sections really
# good its marketing"): the candlelit table, and the salon open onto the mountains.
PHOTO_PAGES = ("/events/weddings", "/events/private", "/events/photoshoots")


def _hex(value):
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _lum(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def run():
    s = Suite("The tops of the pages, as the audit found them")
    client = m.app.test_client()
    root = m.app.root_path
    with open(os.path.join(root, "static", "gudanes.css"), encoding="utf-8") as fh:
        base = fh.read()
    with open(os.path.join(root, "static", "gudanes-pictures.css"), encoding="utf-8") as fh:
        pics = fh.read()

    s.section("The pages")
    for path in PAGES:
        page = client.get(path).get_data(as_text=True)
        hero = re.search(r'<section class="g-hero[^"]*\bg-hero--art\b[^"]*">(.*?)</section>', page, re.S)
        s.check(f"{path} opens on the art hero, with its filled button",
                hero is not None and "g-btn--solid" in hero.group(1), detail="" if hero else "no art hero")
    for path in PHOTO_PAGES:
        page = client.get(path).get_data(as_text=True)
        hero = re.search(r'<section class="g-hero[^"]*">(.*?)</section>', page, re.S)
        body = hero.group(1) if hero else ""
        s.check(f"{path} opens on a photograph, its words in the photograph hero's frame, with its filled button",
                'class="g-hero__img"' in body and 'class="g-hero__in"' in body and "g-btn--solid" in body
                and "g-hero--art" not in (hero.group(0)[:80] if hero else ""), detail=body[:200])

    s.section("The button on navy")
    tokens = dict(re.findall(r"--([a-z-]+):\s*(#[0-9A-Fa-f]{3,6})\b", base))
    navy = tokens.get("blue-deep")
    s.check("the navy is a colour the sheet names", bool(navy), detail=str(sorted(tokens)[:12]))
    navy_rule = pics.find(".g .g-btn--solid, .g .g-herosearch__go")
    art = re.search(r"\.g \.g-hero--art \.g-btn--solid\{([^}]*)\}", pics)
    s.check("its own rule comes after the navy filled-button rule it has to beat",
            art is not None and navy_rule >= 0 and art.start() > navy_rule,
            detail=f"navy at {navy_rule}, art at {art.start() if art else None}")
    fill = re.search(r"background:\s*var\(--([a-z-]+)\)", art.group(1)) if art else None
    words = re.search(r"(?<!-)color:\s*var\(--([a-z-]+)\)", art.group(1)) if art else None
    fill_hex = tokens.get(fill.group(1)) if fill else None
    words_hex = tokens.get(words.group(1)) if words else None
    s.check("a light fill on the navy, not navy on navy",
            bool(fill_hex and navy) and _contrast(_hex(fill_hex), _hex(navy)) >= 4.5,
            detail=f"{fill_hex} on {navy}")
    s.check("and navy words on it, readable",
            bool(fill_hex and words_hex) and _contrast(_hex(fill_hex), _hex(words_hex)) >= 4.5,
            detail=f"{words_hex} on {fill_hex}")

    s.section("Room for it")
    s.check("the words have room below them, so the hero's edge does not cut the button",
            re.search(r"\.g \.g-hero--art > \.g-wrap\{[^}]*padding-bottom:\s*var\(--s[6-9]\)", pics) is not None)
    phone = re.search(r"@media \(max-width: 34rem\)\{\s*(\.g \.g-hero--art\{[^}]*\}\s*\.g \.g-hero--art \.g-hero__art\{[^}]*\})",
                      pics)
    s.check("on a phone the hero is the height of its words, not 88svh of empty navy",
            phone is not None and "min-height: 0" in phone.group(1),
            detail=phone.group(1) if phone else "no phone rule")
    s.check("with the art moved up out of the words' way",
            phone is not None and re.search(r"\.g-hero__art\{[^}]*top:\s*var\(--s4\);\s*bottom:\s*auto", phone.group(1))
            is not None)

    s.section("/history: the postcard and its dates")
    history = client.get("/history").get_data(as_text=True)
    home = client.get("/").get_data(as_text=True)
    s.check("the hero is the postcard, drawn as a g-hero__img in g-hist-hero",
            re.search(r'<section class="g-hero g-hist-hero">\s*<img\b[^>]*class="g-hero__img"[^>]*postcard-chateau-de-gudannes',
                      history) is not None)
    pos = re.search(r"\.g \.g-hist-hero \.g-hero__img\{\s*object-position:\s*50% (\d+)%;\s*\}", pics)
    s.check("cropped from the drive up, so its printed title at the top goes on a wide screen",
            pos is not None and int(pos.group(1)) >= 70, detail=pos.group(0) if pos else "no rule")
    tag = re.search(r"\.g \.g-hero \.g-hist-tag\{([^}]*)\}", pics)
    colour = re.search(r"(?<!-)color:\s*(#[0-9A-Fa-f]{6})", tag.group(1)) if tag else None
    old_gold = re.search(r"\.g \.g-hist-tag\{[^}]*#([0-9A-Fa-f]{6})\)", base)
    s.check("the dates over a photograph carry the eyebrow's shadow",
            tag is not None and "text-shadow: 0 1px 12px rgba(10,22,40,.85)" in tag.group(1),
            detail=tag.group(0) if tag else "no rule")
    s.check("in a lighter gilt than the one that sat on the grey",
            colour is not None and old_gold is not None
            and _lum(_hex(colour.group(1))) > _lum(_hex(old_gold.group(1)))
            and _contrast(_hex(colour.group(1)), (10, 22, 40)) >= 7,
            detail=f"{colour and colour.group(1)} against {old_gold and old_gold.group(1)}")
    s.check("the same dates are in both heroes the rule is for, /history's and the home page's",
            all(re.search(r'<section class="g-hero[^"]*">.*?class="g-hist-tag[^"]*"', page, re.S) for page in (history, home)))

    s.section("The currency control over a photograph")
    s.check("the header carries it, built beside Book by the page's script",
            "sel.className = 'g-curtop__s';" in history and "document.querySelector('.g-top .g-booksel')" in history)
    s.check("over a hero it turns white with the header's other words",
            re.search(r"body\.has-hero \.g-top:not\(\.is-solid\) \.g-curtop__s\{\s*color:\s*#fff;\s*\}", pics) is not None
            and "body.has-hero .g-top:not(.is-solid) .g-nav a{ color: #fff; }" in base)
    s.check("which is more particular than the rule that gives it the header's navy",
            re.search(r"\.g \.g-curtop__s\{[^}]*color:\s*inherit", pics) is not None)
    return s


if __name__ == "__main__":
    print(run().report())
