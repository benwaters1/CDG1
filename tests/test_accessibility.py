"""What a screen reader and a keyboard find on the pages guests use.

The public site is the shopfront and nobody had ever driven it without a
mouse. This is a source check for the same reasons test_table_overflow is
one: it costs nothing, it runs on every commit, and it fails on the page
somebody adds next week rather than the next time anyone thinks to try.

FOUR THINGS, and each of them failed silently while the page looked perfect:

  - A DECORATIVE SVG THAT IS NOT HIDDEN. Twenty-seven of them. None was the
    only content of a link, so nothing was unusable — the reader simply
    walked into ornament between the guest and the sentence they were
    trying to hear. The rest of the site already marked them aria-hidden,
    which is what made these easy to miss.
  - A FIELD WITH NO LABEL. A placeholder is not a label: it disappears the
    moment you type, it is not announced everywhere, and it gives the tap
    target nothing to grow into. One of the two was a message box added the
    day before this file existed.
  - A ROW HEADER WRITTEN AS A CELL. "Balance due" and "€120.00" in two
    plain cells are read as two unrelated numbers. As th scope="row" they
    are read as one fact. This is the statement — the page most likely to
    be opened on a phone and the one where hearing the wrong figure costs
    the most.
  - A CONTROL WITH NO NAME AT ALL. An icon-only link is a link a reader
    announces as "link" and nothing else. There are none today; this is
    the check that keeps it that way.

WHAT IS DELIBERATELY NOT HERE. Colour contrast, which needs the rendered
page rather than the source, and heading order, which has too many false
positives on templates that compose. Both are worth doing with a browser;
neither is worth a check that cries wolf, because a check nobody trusts is
one everybody learns to skip — and it will be skipped on the commit that
mattered.
"""
import os
import re

from _harness import Suite
import _harness

TEMPLATES = os.path.join(_harness.ROOT, "templates")


def public_templates():
    """Everything a guest can reach, found by what it extends.

    Asked of the files rather than listed here, so a new public page is in
    this sweep the moment it exists — which is the only version of this test
    that catches the page nobody remembered to add.
    """
    out = {}
    for name in sorted(os.listdir(TEMPLATES)):
        if not name.endswith(".html"):
            continue
        src = open(os.path.join(TEMPLATES, name), encoding="utf-8").read()
        if 'extends "public_base.html"' in src or name == "public_base.html":
            out[name] = src
    return out


def _visible_text(html):
    """What is left once the markup and the Jinja are taken out."""
    text = re.sub(r"<svg.*?</svg>", "", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{[{%].*?[%}]\}", "X", text)
    return text.strip()


def run():
    s = Suite("Using the site without a mouse")
    pages = public_templates()

    s.check(f"there are {len(pages)} public pages to check", len(pages) > 20,
            detail="found by what they extend, not from a list, so a new page "
                   "is swept the moment it exists")

    s.section("Nothing is announced that is only decoration")
    # An SVG with no aria-hidden is read out. None of these is the sole
    # content of a control (that is the next section), so every one of them
    # is ornament sitting between a guest and the sentence they want.
    loud = []
    for name, src in pages.items():
        for mo in re.finditer(r"<svg\b[^>]*>", src, re.I):
            if not re.search(r'aria-hidden|aria-label|role\s*=\s*"img"', mo.group(0), re.I):
                loud.append(f"{name}: {mo.group(0)[:60]}")
    s.check("every decorative svg is hidden from the reader", not loud,
            detail=f"{len(loud)} not hidden, e.g. {loud[:2]}" if loud else "")

    s.section("Everything you can operate says what it is")
    nameless = []
    for name, src in pages.items():
        for mo in re.finditer(r"<(a|button)\b[^>]*>(.*?)</\1>", src, re.I | re.S):
            if _visible_text(mo.group(2)):
                continue
            if re.search(r"aria-label|aria-labelledby", mo.group(0), re.I):
                continue
            nameless.append(f"{name}: {mo.group(0)[:70]}")
    s.check("no link or button is left without a name", not nameless,
            detail=f"{len(nameless)}: {nameless[:2]}" if nameless else "",
            )
    s.check("and the check can tell the difference",
            not _visible_text('<a href="/x"><svg><path d="M0 0"/></svg></a>')
            and _visible_text('<a href="/x"><svg></svg> Book</a>') == "Book",
            detail="an icon-only link must read as nameless and a labelled one "
                   "must not, or the sweep above passes on everything")

    s.section("Every field a guest types into has a label")
    unlabelled = []
    for name, src in pages.items():
        labelled_ids = set(re.findall(r'<label[^>]*\bfor\s*=\s*"([^"]+)"', src, re.I))
        for mo in re.finditer(r"<(input|select|textarea)\b[^>]*>", src, re.I):
            tag = mo.group(0)
            typ = (re.search(r'type\s*=\s*"([^"]+)"', tag, re.I) or [None, "text"])[1].lower()
            if typ in ("hidden", "submit", "button", "image"):
                continue
            if re.search(r"aria-label|aria-labelledby", tag, re.I):
                continue
            fid = re.search(r'\bid\s*=\s*"([^"]+)"', tag, re.I)
            if fid and fid.group(1) in labelled_ids:
                continue
            before = src[max(0, mo.start() - 260):mo.start()]
            if "<label" in before and "</label>" not in before[before.rfind("<label"):]:
                continue                      # wrapped in a label, which counts
            unlabelled.append(f"{name}: {tag[:70]}")
    s.check("no field is left to a placeholder alone", not unlabelled,
            detail=f"{len(unlabelled)}: {unlabelled[:2]}" if unlabelled else "")

    s.section("A table of figures reads as rows, not as two columns")
    # A label/value table with no th at all is read as unrelated cells: the
    # guest hears every label, then every number, and has to hold the pairing
    # in their head.
    headerless = []
    for name, src in pages.items():
        for mo in re.finditer(r"<table\b.*?</table>", src, re.I | re.S):
            if "<th" not in mo.group(0).lower():
                headerless.append(f"{name}: {mo.group(0)[:50]}")
    s.check("every table has header cells of some kind", not headerless,
            detail=f"{len(headerless)}: {headerless[:2]}" if headerless else "")

    statement = pages.get("guest_statement.html", "")
    # Named one at a time rather than counted. "At least three" passes with any
    # one of the four turned back into a plain cell, including the balance --
    # which is the figure the whole page exists for. A threshold that tolerates
    # exactly the regression it was written for is not a check.
    rows = [
        ("the total", r'<th scope="row"><strong>Total</strong></th>'),
        ("what has been paid", r'<th scope="row">Paid</th>'),
        ("the balance still owed", r"""<th scope="row"><strong>\{\{ 'Balance due'"""),
        ("and the tourist tax", r'<th scope="row">\s+<strong>Taxe'),
    ]
    for what, pattern in rows:
        s.check(f"{what} is a row header, not a loose cell",
                re.search(pattern, statement) is not None,
                detail="read as a plain cell, a guest hears the label and the "
                       "figure as two unrelated things")

    s.section("The things that were already right, and must stay right")
    base = pages.get("public_base.html", "")
    s.check("the page declares its language", '<html lang="{{ lang }}"' in base,
            detail="hardcoded English would have a reader pronounce the French "
                   "site in English")
    s.check("and it follows the language actually chosen", "{{ lang }}" in base,
            detail="lang is set from current_language(), not fixed")
    s.check("there is a skip link past the navigation",
            'class="g-skip"' in base and 'href="#main"' in base,
            detail="without it, every page starts with the whole menu again")
    s.check("and something with that id to skip to",
            'id="main"' in base, detail="a skip link pointing at nothing is worse "
                                        "than none, because it looks handled")

    css_path = os.path.join(_harness.ROOT, "static", "gudanes.css")
    css = open(css_path, encoding="utf-8").read() if os.path.exists(css_path) else ""
    s.check("focus is visible when tabbing", ":focus-visible" in css,
            detail="a keyboard user with no focus ring cannot see where they are")
    s.check("and the outline is not switched off wholesale",
            not re.search(r"\*\s*\{[^}]*outline\s*:\s*(none|0)", css),
            detail="a blanket outline:none is the single most common way a site "
                   "becomes unusable by keyboard")

    s.section("Nobody has rearranged the tab order by hand")
    positive = []
    for name, src in pages.items():
        for mo in re.finditer(r'tabindex\s*=\s*"([0-9]+)"', src, re.I):
            if int(mo.group(1)) > 0:
                positive.append(f"{name}: {mo.group(0)}")
    s.check("no positive tabindex anywhere", not positive,
            detail=f"{positive[:3]} — one of these pulls an element to the front "
                   "of the tab order for the whole page, not just its own section")

    return s


if __name__ == "__main__":
    print(run().report())
