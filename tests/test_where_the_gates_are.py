"""Where the château is. One answer, or none — never four that disagree.

This codebase carried FOUR answers to "where are the gates", and they were
spread over four and a half kilometres:

  42.8083, 1.6528   typed into the contact page's copy, and published as
                    structured data on every public page — which is the pin
                    behind Google's own "Directions" button, tappable by a
                    guest who never opens the site
  42.7847, 1.6564   the default inside the handover's own map macro, and
                    before that inside the email
  42.7669, 1.6647   the weather constant, commented "the chateau's own
                    coordinates"

None had been checked against the gates. Nothing errored, every page rendered,
and the numbers looked authoritative because a number always does.

  IT MATTERS ON THIS ROAD. The last four kilometres climb, narrow and are
  unlit, and the caption the design side wrote for the map says "the pin is
  the gates, not the village". That is a CLAIM. Printed over an unchecked pin,
  at night, it is worse than saying nothing — the reason the map exists at all
  is that being NEARLY right sends people to the village square.

  SO: ABSENT, NOT GUESSED. house_pin() reads a setting and returns None until
  somebody stands at the gates with a telephone. With it unset the pages keep
  the address, the road, and "telephone and someone will come down" — all of
  which are true. With it set, every one of them shows the same pin.

  AND NO TEMPLATE MAY HARD-CODE ONE AGAIN. The handovers arrive as whole-file
  replacements and have already put the same figure back twice, so this is
  checked at the source rather than trusted.
"""
import os
import re

from _harness import Suite, clients, db
import _harness

m = _harness.m
TEMPLATES = os.path.join(_harness.ROOT, "templates")

# A coordinate for THIS valley, in a template.
#
# SIDE BY SIDE WAS NOT ENOUGH, and that was the first version of this. The
# macro sets them on two separate lines —
#     {% set lat = settings.get('lat', '42.7847') %}
#     {% set lng = settings.get('lng', '1.6564') %}
# — so a pair-shaped pattern walked straight past the exact thing that has now
# come back twice. A lone one counts.
#
# Four decimal places is what keeps this off money: a price in this app has
# two, and a coordinate has four or more.
PAIR = re.compile(
    r"4[12]\.\d{3,}\s*[,/]\s*1\.\d{3,}"      # the two together
    r"|4[12]\.\d{4,}"                        # a latitude on its own
    r"|(?<![\d.])1\.6\d{3,}")                # a longitude in this valley


def _pin(lat, lng):
    conn = db()
    for key, value in (("house_lat", lat), ("house_lng", lng)):
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        if value is not None:
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)",
                         (key, value))
    conn.commit()
    conn.close()


def run():
    s = Suite("Where the gates are")
    anon = m.app.test_client()
    try:
        s.section("No template states a pin of its own")
        # The one that keeps coming back. Two handovers have now shipped
        # 42.7847/1.6564 as a default, and it is not the figure the contact
        # page used to print, and neither is the weather constant.
        offenders = []
        for name in sorted(os.listdir(TEMPLATES)):
            if not name.endswith(".html"):
                continue
            body = open(os.path.join(TEMPLATES, name), encoding="utf-8").read()
            # Comments are where the history is written down, and the history
            # has to be allowed to quote the numbers it is about.
            body = re.sub(r"\{#.*?#\}", " ", body, flags=re.S)
            body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
            if PAIR.search(body):
                offenders.append("%s: %s" % (name, PAIR.search(body).group(0)))
        s.check("not one of them", not offenders,
                detail="a figure typed into a page is right until somebody "
                       "measures it, and nobody has: " + str(offenders))

        s.section("With none set, nothing pretends to know")
        _pin(None, None)
        with m.app.test_request_context("/"):
            s.check("house_pin says so plainly", m.house_pin() is None,
                    detail=str(m.house_pin()))
        page = anon.get("/contact").get_data(as_text=True)
        s.check("the contact page draws no map button",
                "maps.apple.com" not in page and "query=42" not in page,
                detail="being nearly right is the failure this map was drawn "
                       "to prevent")
        s.check("and does not claim the pin is the gates",
                "The pin is the gates" not in page,
                detail="the caption is a claim; it stands or falls with a "
                       "checked pin")
        s.check("but still says how to actually get in",
                "Telephone when you leave Les Cabannes" in page,
                detail="which is true whether or not anybody has measured "
                       "anything")
        s.check("and Google is told nothing rather than something wrong",
                '"geo"' not in page,
                detail="with no geo, Google falls back to the address, which "
                       "is honest — that button is tappable by somebody who "
                       "never opens this site")

        s.section("With one set, every page shows the same one")
        _pin("42.8083", "1.6528")
        page = anon.get("/contact").get_data(as_text=True)
        s.check("the map buttons appear",
                "maps.apple.com" in page and "42.8083,1.6528" in page,
                detail="the coordinates go in the link, not an address search")
        s.check("the caption comes back with them",
                "The pin is the gates" in page)
        s.check("and Google is given the same figure",
                '"latitude": 42.8083' in page and '"longitude": 1.6528' in page,
                detail="the structured data and the button a guest taps have "
                       "to be the same place")
        # THE POINT OF ONE DEFINITION. Move it, and everything moves.
        _pin("42.9", "1.7")
        page = anon.get("/contact").get_data(as_text=True)
        s.check("moving it moves all of them at once",
                "42.9,1.7" in page and '"latitude": 42.9' in page
                and "42.8083" not in page,
                detail="four figures in four files is how they came to "
                       "disagree by four and a half kilometres")

        s.section("No page offers a search dressed up as a map")
        # Six templates ran a Google Maps SEARCH for the house by name. A
        # search returns Google's best guess, weighted by where the person
        # searching is — from outside Europe it answered in Tokyo. That is
        # worse than the village square the design side was worried about, and
        # worse than no link, because the guest sets off.
        import os as _os
        searched = []
        for filename in sorted(_os.listdir(TEMPLATES)):
            if not filename.endswith(".html"):
                continue
            body = open(_os.path.join(TEMPLATES, filename),
                        encoding="utf-8").read()
            body = re.sub(r"\{#.*?#\}", " ", body, flags=re.S)
            if "query=Ch" in body or "maps?q=Ch" in body:
                searched.append(filename)
        s.check("not one of them searches by name", not searched,
                detail="a name search is not a pin, and it does not even stay "
                       "in the right country: " + str(searched))

        # AND app.py, because the link building MOVED THERE. The first version
        # of this guard read the templates only — right while the six copies
        # lived in templates, wrong the moment they became one builder in
        # Python. Moving the thing being guarded out of the guarded region is
        # its own fault class, and breaking it on purpose is what found it.
        source = open(os.path.join(_harness.ROOT, "app.py"),
                      encoding="utf-8").read().replace("\r\n", "\n")
        code = "\n".join(l.split("#")[0] for l in source.splitlines())
        s.check("and neither does the one place that builds them",
                "query=Ch" not in code and "maps?q=Ch" not in code,
                detail="a guard that reads only templates would not see a "
                       "name search move into Python")
        # Only these three functions are read, so the weather constant and the
        # docstrings that quote all four figures are left alone.
        pinned = ""
        for fn in ("def house_pin(", "def house_coordinates(",
                   "def house_map_links("):
            at = source.find(fn)
            if at >= 0:
                end = source.find("\ndef ", at + 1)
                pinned += source[at:end if end > 0 else len(source)]
        pinned = re.sub(r'""".*?"""', " ", pinned, flags=re.S)
        pinned = "\n".join(l.split("#")[0] for l in pinned.splitlines())
        s.check("and no coordinate is written into the pin functions",
                not PAIR.search(pinned),
                detail="an invented default here would reach every page at "
                       "once, which is the whole point of there being one: "
                       + str(PAIR.findall(pinned)[:3]))

        _pin(None, None)
        page = anon.get("/contact").get_data(as_text=True)
        s.check("with no pin the map itself is absent, not wrong",
                "output=embed" not in page,
                detail="a map showing the wrong place is worse than a link to "
                       "one, because it looks authoritative")
        s.check("and the page says why rather than showing an empty box",
                "does not find them" in page)
        _pin("42.8083", "1.6528")
        page = anon.get("/contact").get_data(as_text=True)
        s.check("with a pin the embedded map uses it",
                "maps?q=42.8083,1.6528" in page and "output=embed" in page,
                detail="the picture and the button have to be one place")
        for path, name in (("/book/rooms", "the room page"),
                           ("/contact", "the contact page")):
            r = anon.get(path)
            if r.status_code != 200:
                continue
            body = r.get_data(as_text=True)
            s.check("%s links to the pin" % name,
                    "query=42.8083,1.6528" in body,
                    detail=path)

        s.section("The weather is allowed its own, and says why")
        source = open(os.path.join(_harness.ROOT, "app.py"),
                      encoding="utf-8").read()
        s.check("it is still a constant",
                "WEATHER_LAT, WEATHER_LON = " in source)
        s.check("and it no longer calls itself the château's coordinates",
                "# The chateau's own coordinates." not in source,
                detail="named that, it reads as THE answer — and it is four "
                       "and a half kilometres from the one on the contact page")
        s.check("the comment says what it is instead",
                "WHERE TO ASK ABOUT THE WEATHER" in source,
                detail="a forecast for a valley is the same figure four "
                       "kilometres up it; a pin is not")
    finally:
        _pin(None, None)
    return s
