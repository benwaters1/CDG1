"""The Estate page (/facilities), 24 November 2026 (the owner's brief, section 10).

The owner, with screenshots of the page on a desktop: "fix the formatting here,
then also add cycling".

  - LE DOMAINE sat in the left half of a wide screen with the right half empty,
    and the live weather line ("At the château 15°C overcast") was centred under
    words that were left-aligned. It sits beside a photograph of the grounds
    now, and the weather is one line at the words' left edge.
  - "On a workshop" -- the site calls them retreats.
  - ARRANGED FOR YOU had three cards in a row of four, leaving a hole on the
    right. Cycling makes four: up the Tour de France climb to Plateau de Beille,
    which starts at Les Cabannes, at the foot of the drive.

What breaks quietly, and is checked:

  - A drawing that draws nothing. illus() is one long if/elif: a kind it does not
    know renders an empty <svg>, which shows as a blank card top and errors
    nowhere. The cycle must have strokes in it.
  - The centring coming back. .g-wx is centred by a general rule (margin-inline:
    auto) for the pages that show it on its own; the rule for this section has
    to win, and no rule for this section may centre it.
  - Another page's hero. The pool from the air, the brief's first suggestion, is
    the home page's still on a phone; this one is the gardens from the air.
"""
import json
import os
import re
from datetime import datetime, timezone

from _harness import Suite, visible_text
import _harness

m = _harness.m
ROOT = _harness.ROOT
KEY = "weather_snapshot"
PHOTO = "img/rk/from-the-air-parterre.jpg"


def css_rules(text):
    """(selector, body) for every rule, media blocks opened up."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.findall(r"([^{}@]+)\{([^{}]*)\}", text)


def run():
    s = Suite("The Estate: Le Domaine beside the grounds, and cycling")
    conn = _harness.db()
    keep = conn.execute("SELECT value FROM app_settings WHERE key = ?", (KEY,)).fetchone()
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (KEY, json.dumps({"c": 15, "code": 3, "at": datetime.now(timezone.utc).isoformat()})))
    conn.commit()
    try:
        r = m.app.test_client().get("/facilities")
    finally:
        if keep is None:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (KEY,))
        else:
            conn.execute("UPDATE app_settings SET value = ? WHERE key = ?", (keep["value"], KEY))
        conn.commit()
        conn.close()
    if not s.check("/facilities loads", r.status_code == 200, detail=str(r.status_code)):
        return s
    page = r.get_data(as_text=True)
    words = visible_text(page)

    s.section("Le Domaine, beside a photograph of the grounds")
    dom = re.search(r'<section class="g-sec g-domaine">(.*?)</section>', page, re.S)
    dom = dom.group(1) if dom else ""
    s.check("the section is there", bool(dom))
    s.check("its words and a photograph side by side", '<div class="g-sp__text">' in dom and 'class="g-sp__main"' in dom)
    s.check("the photograph is the gardens from the air", PHOTO.split("/")[-1].rsplit(".", 1)[0] in dom)
    heroes = []
    for name in os.listdir(os.path.join(ROOT, "templates")):
        if not name.endswith(".html"):
            continue
        src = open(os.path.join(ROOT, "templates", name), encoding="utf-8").read()
        for tag in re.findall(r"<(?:img|video)\b[^>]*>", src):
            if PHOTO in tag and ("g-hero__img" in tag or "data-phone-still" in tag or "poster=" in tag):
                heroes.append(name)
    s.check("and no page's hero", not heroes, detail=", ".join(heroes))

    s.section("The weather, one line at the words' left edge")
    s.check("the reading is shown", 'class="g-wx"' in dom)
    first = dom.find("g-lede-first")
    wx = dom.find('class="g-wx"')
    after = dom.find("On a retreat")
    s.check("directly under the first paragraph", 0 <= first < wx < after
            and "<p" not in dom[dom.find("</p>", first) + 4:wx], detail=f"{first} {wx} {after}")
    sheets = {n: open(os.path.join(ROOT, "static", n), encoding="utf-8").read()
              for n in ("gudanes.css", "gudanes-pictures.css")}
    centred = [(n, sel.strip()) for n, txt in sheets.items() for sel, body in css_rules(txt)
               if "g-domaine" in sel and "g-wx" in sel and re.search(r"text-align:\s*center", body)]
    s.check("no rule centres it in this section", not centred, detail=str(centred))
    own = [body for sel, body in css_rules(sheets["gudanes-pictures.css"])
           if re.fullmatch(r"\s*\.g \.g-domaine \.g-wx\s*", sel)]
    s.check("and this section's rule sets it to the left, with no auto margin to centre it",
            bool(own) and all("text-align: left" in b and re.search(r"margin:\s*var\(--s2\)\s+0\s", b) for b in own),
            detail=str(own))
    # Negative control: the rule the owner saw is there to be beaten, so the check above is not checking nothing.
    s.check("(the general rule that centred it is still there to be beaten)",
            ".g-wx{ max-width: var(--m-read); margin-inline: auto; }" in sheets["gudanes.css"])

    s.section("A retreat, not a workshop")
    s.check("'On a workshop' nowhere on the page", "On a workshop" not in words)
    s.check("On a retreat, between the cooking, the outings and dinner",
            "On a retreat, the grounds are yours every afternoon, between the cooking, the outings and dinner." in words)
    s.check("the button says Discover the retreats", "Discover the retreats" in words
            and "Discover the full experience" not in words)

    s.section("Arranged for You: four, cycling the fourth")
    sec = re.search(r'<section class="g-sec g-sec--sand g-extras-sec"[^>]*>(.*?)</section>', page, re.S)
    sec = sec.group(1) if sec else ""
    cards = re.findall(r'<li class="g-extras__i">(.*?)</li>', sec, re.S)
    names = [visible_text(x) for x in re.findall(r'<p class="g-extras__en">(.*?)</p>', sec, re.S)]
    s.check("four extras", len(cards) == 4, detail=str(len(cards)))
    s.check("cycling among them", "Cycling" in names, detail=str(names))
    cyc = next((c for c in cards if "Cycling" in c), "")
    s.check("Le vélo, up the Tour de France climb to Plateau de Beille",
            "Le vélo" in cyc and "Up the Tour de France climb to Plateau de Beille" in visible_text(cyc))
    art = re.search(r"<svg\b.*?</svg>", cyc, re.S)
    art = art.group(0) if art else ""
    s.check("its drawing draws: a bicycle's two wheels and a frame", art.count("<circle") >= 4 and art.count("<path") >= 6,
            detail=f"{art.count('<circle')} circles, {art.count('<path')} paths")
    s.check("in the same hand as the others", 'stroke-width="1.15"' in art and 'viewBox="0 0 96 96"' in art)
    with m.app.test_request_context("/"):
        empty = m.app.jinja_env.from_string("{% from '_illus.html' import illus %}{{ illus('no-such-drawing') }}").render()
    # Negative control: an unknown kind draws an empty frame, which is the fault the count above would catch.
    s.check("(an unknown drawing is an empty frame, so the count above means something)",
            "<path" not in empty and "<circle" not in empty)
    s.check("no price and no supplier on the card", not re.search(r"€|\d+\s?(?:EUR|euros)", visible_text(cyc)))
    grid = [(sel.strip(), body) for sel, body in css_rules(sheets["gudanes-pictures.css"]) if ".g-extras-sec .g-extras" in sel]
    cols = [re.search(r"grid-template-columns:\s*([^;]+)", b).group(1).strip() for _, b in grid
            if "grid-template-columns" in b]
    s.check("one column on a phone, two on a tablet, four on a desk",
            cols == ["minmax(0, 1fr)", "repeat(2, minmax(0, 1fr))", "repeat(4, minmax(0, 1fr))"], detail=str(cols))
    return s


if __name__ == "__main__":
    print(run().report())
