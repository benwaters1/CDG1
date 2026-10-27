"""How many rooms the house lets, and what the cheapest one costs.

Both facts are owned by the `rooms` table. Both were written in prose on ten
public pages — "Five bedrooms", "from €220 a night" — and both are true this
morning. Add a sixth room, or put the Cerise up to €250, and ten pages are
wrong at once. One of them carries the first figure a guest ever reads.

This is the deposit lie a third time. The room deposit reached four pages and
the workshop deposit reached one, both for the same reason: a fact about the
house written into a sentence instead of read from the thing that knows it.

WHY THIS ONE IS NOT SIMPLY TEMPLATED AWAY.

The price is a figure and is now read — on the home page and in the French
meta description, the two places a number stands alone. The COUNT is not a
figure; it is prose, twenty-four times over, in narrative ("Five bedrooms are
finished. Eighty-nine are not."), in section headings, in French, and in copy
the design side rewrites on every handover. Turning all of it into template
tags would fight every one of those handovers and turn readable sentences into
soup, and the next handover would put the sentences back.

So the count was CHECKED instead: this suite read the rooms table and named
every template line that disagreed with it.

17 OCTOBER 2026 (z28): THE DESIGN SIDE TEMPLATED IT AFTER ALL, for the sixth
room. Every claim now reads house_rooms.count_word, so the site says five
while the sixth room is hidden and six the moment it is ticked Bookable, with
nothing to edit on the day. The source check stays and still names any count
a handover types back into a sentence; the claims themselves are now checked
in the pages AS SERVED, and a room is put on sale to prove they follow it.
That is stronger than the source check was: a template can no longer
disagree with the table at all.
"""
import glob
import html as htmllib
import os
import re

from _harness import Suite, clients, db

import _harness

m = _harness.m

PUBLIC_PAGES = [
    "home", "book_rooms", "book_room", "workshops_public", "restoration",
    "facilities", "events_weddings", "events_private", "restaurant_info",
    "events_info", "contact", "press", "story",
]

WORD = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
        7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
        12: "twelve"}

# The written-out count, in English and in French, followed by a word meaning
# a room. Comments are stripped first: a number inside {# #} is a note about
# the page, not a claim the page makes — the mistake this repo has made four
# times.
COUNTS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "un": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six ": 6,
    "sept": 7, "huit": 8, "neuf": 9, "dix": 10,
}
# (?<![-\w]) so "Ninety-Four Rooms" does not read as "four rooms". That
# phrase is the house's own motto and is on nearly every public page, so
# without this the guard reported thirty disagreements that were all the same
# true sentence -- and a guard that cries wolf on the front page gets switched
# off inside a week.
#
# "guest rooms" since 15 October (owner, handover z22): the house has more
# bedrooms than it lets, so the pages say "five guest rooms". "bedrooms" stays
# in, because a page that went back to saying it would still be a claim. Not
# bare "rooms": "the Family Suite or three rooms" in the room finder is about a
# party's size, not how many the house lets.
CLAIM = re.compile(
    r"(?<![-\w])(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"\s+(bedrooms|guest\s+rooms)\b", re.I)

# The one match that is HISTORY rather than a claim about today. The
# restoration timeline says five bedrooms against 2022 because five was true
# in 2022, and it stays right whatever the house opens next.
#
# Checked in both directions, like COVERAGE_KNOWN_GAPS: a new exception reds
# the run, and so does one that stops matching and is left on the list.
# The restoration timeline's 2022 entry went in the 1 October handover's cuts,
# so nothing is excused now. The machinery stays for the next piece of history.
HISTORICAL = {}


# The pages as a visitor is served them. Paths, not endpoints: these are the
# addresses on the menu. The room page is added at run time, by id.
RENDERED = [
    "/", "/book", "/workshops", "/restoration", "/facilities", "/events/weddings",
    "/contact", "/press", "/story", "/history", "/gallery", "/social",
]


# A lettings claim as the served pages make it: "six guest rooms", "The Six
# Rooms", "all six rooms". Not "bedrooms" here: the Family Suite is "two
# bedrooms" on every card, which is the suite, not the house. Not bare
# "N rooms" either: "the Family Suite or three rooms" is a party's size.
_N = r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
SERVED_CLAIM = re.compile(
    r"(?<![-\w])" + _N + r"\s+guest\s+rooms\b"
    r"|\b(?:the|all)\s+" + _N + r"\s+rooms\b", re.I)


def _visible(page):
    """The words a visitor reads: scripts, styles and tags out, entities read."""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(htmllib.unescape(text).split())


def _blank(match):
    """Replace a block with spaces, keeping every newline it contained.

    Removing the block instead shifts every line number after it, and a guard
    that names the wrong line is worse than one that names no line at all --
    somebody goes and looks.
    """
    return re.sub(r"[^\n]", " ", match.group(0))


def _prose(path):
    """A page's visible prose, with comments and template tags blanked out.

    Blanked rather than deleted, so line numbers still point where they say.
    A number inside a {# #} is a note about the page, not a claim the page
    makes -- the mistake this repo has made four times.
    """
    raw = open(path, encoding="utf-8", errors="replace").read()
    raw = re.sub(r"\{#(?:.|\n)*?#\}", _blank, raw)
    raw = re.sub(r"\{[%{].*?[%}]\}", _blank, raw, flags=re.S)
    return raw


def run():
    s = Suite("What the public pages say the house has")
    _oc, _ec, _owner, _emp = clients()
    conn = db()
    facts = m.house_room_facts(conn)

    s.section("The house knows both facts")
    s.check("it counts the rooms it lets", facts["count"] > 0,
            detail=str(facts))
    s.check("and says the count in words, because the prose does",
            facts["count_word"] == WORD.get(facts["count"], str(facts["count"])),
            detail=str(facts["count_word"]))
    s.check("with the cheapest room's price",
            facts["from_price"] is not None and facts["from_price"] > 0,
            detail=str(facts["from_price"]))

    s.section("It counts the rooms the house LETS, and prices the cheapest")
    # The catalogue has five active rooms and every one priced, so both of
    # these branches were dead code as far as this suite was concerned: a
    # version counting retired rooms, or reporting "from EUR0 a night",
    # passed everything. Built here and taken away again -- the rooms are
    # live data and this suite has no business retiring one.
    conn.execute(
        """INSERT INTO rooms (name, export_token, active, max_occupancy,
                   price_per_night, sort_order)
           VALUES ('zz retired', 'zz-retired-token', 0, 2, 90.0, 99)""")
    conn.commit()
    with_retired = m.house_room_facts(conn)
    s.check("a room that is not let does not change the count",
            with_retired["count"] == facts["count"],
            detail=f"{with_retired['count']} vs {facts['count']}")
    s.check("nor make itself the price the page opens with",
            with_retired["from_price"] == facts["from_price"],
            detail=f"a retired room at 90 would have undercut the cheapest "
                   f"real one at {facts['from_price']}")

    conn.execute("UPDATE rooms SET active = 1, price_per_night = 0 "
                 "WHERE export_token = 'zz-retired-token'")
    conn.commit()
    free = m.house_room_facts(conn)
    s.check("a room with no price does not become a price of nought",
            free["from_price"] == facts["from_price"],
            detail=f"{free['from_price']} — 'from EUR0 a night' on the line "
                   "guests read first is worse than no price at all")
    conn.execute("DELETE FROM rooms WHERE export_token = 'zz-retired-token'")
    conn.commit()

    # And the branch that only fires when NOTHING is priced. The NULLIF in
    # the query handles one unpriced room on its own, so the Python fallback
    # was never reached — a version returning 0 instead of None passed
    # everything above. Every price put back afterwards, and checked.
    priced = {r["id"]: r["price_per_night"] for r in conn.execute(
        "SELECT id, price_per_night FROM rooms WHERE active = 1")}
    conn.execute("UPDATE rooms SET price_per_night = 0 WHERE active = 1")
    conn.commit()
    nothing = m.house_room_facts(conn)
    for rid, was_price in priced.items():
        conn.execute("UPDATE rooms SET price_per_night = ? WHERE id = ?",
                     (was_price, rid))
    conn.commit()
    s.check("with no room priced at all, there is no price to state",
            nothing["from_price"] is None,
            detail=f"{nothing['from_price']} — 'from €0 a night' is worse "
                   "than saying nothing, and the page drops the phrase")
    s.check("and every price is back where it was",
            m.house_room_facts(conn) == facts,
            detail="every suite after this one reads the same catalogue")
    s.check("and the catalogue is left as it was found",
            m.house_room_facts(conn) == facts,
            detail="the rooms are live data; every suite after this one "
                   "reads the same five")

    s.section("The price a guest reads first is read, not written")
    home = " ".join(m.app.test_client().get("/")
                    .get_data(as_text=True).split())
    s.check("the home page states it", str(facts["from_price"]) in home,
            detail=str(facts["from_price"]))
    # Asked of the whole design surface rather than of home.html, because the
    # line moved: it lives in _stay_panel now, which five other pages show as
    # well. Pinning the FILE meant a handover could shuffle the markup into a
    # partial and the check would fail while the page was right -- or, worse,
    # leave the string in home.html beside a figure typed in somewhere else.
    #
    # The property is what matters: the starting rate must be read from the
    # rooms table wherever it is said, and written into a sentence nowhere.
    reads_it, writes_it = [], []
    for name in sorted(os.listdir("templates")):
        if not name.endswith(".html"):
            continue
        body = open(os.path.join("templates", name), encoding="utf-8").read()
        if "house_rooms.from_price" in body:
            reads_it.append(name)
        # Jinja comments skipped as BLOCKS, not by looking for "{#" at the
        # start of a line: the first version of this flagged the explanation
        # written directly above the fix, because a comment's middle lines
        # begin with prose.
        prose = re.sub(r"\{\#.*?\#\}", " ", body, flags=re.S)
        for line in prose.splitlines():
            if "house_rooms" in line:
                continue
            if re.search(r"(from|partir de)\s*<?b?>?\s*(&euro;|€|EUR)\s*%d"
                         % facts["from_price"], line, re.I):
                writes_it.append("%s: %s" % (name, line.strip()[:60]))
    s.check("from the rooms rather than from the template", bool(reads_it),
            detail="a figure typed into a sentence is the deposit lie again, "
                   "on the line more people read than any other")
    s.check("and no template writes the figure into a sentence", not writes_it,
            detail="; ".join(writes_it[:3]))
    french = " ".join(m.app.test_client().get("/book")
                      .get_data(as_text=True).split())
    s.check("and so does the French description",
            str(facts["from_price"]) in french)

    s.section("Every page with the stay panel prices its workshop card")
    # The panel says "From €… per person" when it is handed the workshops and
    # only "Per person" when it is not -- and the Estate page was never handed
    # them, which the design side noticed and the page did not say. The price
    # is the cheapest workshop on offer, by the workshops page's own rule.
    conn = db()
    offer = m.workshops_on_offer(conn, m.house_today())
    conn.close()
    prices = [w["price_per_person"] for w, _s in offer if w["price_per_person"]]
    s.check("there are workshops on offer to price", bool(prices),
            detail="nothing to price, so the next check would prove nothing")
    if prices:
        figure = "{:,.0f}".format(min(prices))
        unpriced = []
        for url in ("/facilities", "/gallery", "/press", "/story", "/"):
            body = m.app.test_client().get(url).get_data(as_text=True)
            # The panel's workshop card, by its words since 26 Oct (the retreats deck).
            if "nothing to pay at the end" not in body or "g-three" not in body:
                continue
            if f"&euro;{figure}" not in body and f"€{figure}" not in body:
                unpriced.append(url)
        s.check(f"each shows the cheapest workshop, €{figure} a person", not unpriced,
                detail=f"only 'Per person' on {unpriced}")

    s.section("Every public page agrees about how many rooms there are")
    # Named rather than counted. "Three pages disagree" sends somebody
    # hunting; a list of file and line is a job that can be finished.
    def claims():
        """(page, line number, line, count said) for every lettings claim."""
        for name in PUBLIC_PAGES:
            path = "templates/%s.html" % name
            if not os.path.exists(path):
                continue
            for i, line in enumerate(_prose(path).splitlines(), 1):
                for word, _n in CLAIM.findall(line):
                    said = COUNTS.get(word.lower().strip())
                    if said is not None:
                        yield name, i, line.strip(), said

    def historical(name, line):
        """Is this one of the sentences that is history rather than a claim?"""
        for (page, marker), _why in HISTORICAL.items():
            if page == name and marker in line:
                return (page, marker)
        return None

    wrong, seen_exceptions = [], set()
    for name, i, line, said in claims():
        was = historical(name, line)
        if was:
            seen_exceptions.add(was)
            continue
        if said != facts["count"]:
            wrong.append(f"{name}:{i} says {said}")
    s.check(f"all of them say {facts['count_word']}", not wrong,
            detail=("; ".join(wrong[:6]) + (f" (+{len(wrong) - 6} more)"
                                            if len(wrong) > 6 else ""))
                   or f"checked against {facts['count']} active room(s)")
    # The source check above still names any count typed into a sentence.
    # Since z28 there should be none to find: every claim reads
    # house_rooms.count_word, so the claims are checked where they now live --
    # on the pages as served (below).

    s.section("The pages as served say how many rooms are on sale (z28 item 7)")
    # z28 made every "five guest rooms / Five Rooms / Cinq Chambres" read the
    # count of rooms on sale, so the site says five while the sixth room is
    # hidden and six the moment it is ticked, with no template edit on the
    # day. The claim is checked in the response, which is stronger than the
    # source: a template can no longer disagree with the table at all, and a
    # count typed back in by a handover is named here as well as above.
    conn = db()   # the workshop section above closed its own
    first_room = conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY sort_order, id LIMIT 1").fetchone()
    urls = list(RENDERED) + ([f"/book/{first_room['id']}"] if first_room else [])

    def served_claims():
        """(url, count said) for every lettings claim in the visible text."""
        anon = m.app.test_client()
        found, answered = [], []
        for url in urls:
            r = anon.get(url)
            if r.status_code != 200:
                continue
            answered.append(url)
            for hit in SERVED_CLAIM.finditer(_visible(r.get_data(as_text=True))):
                found.append((url, COUNTS[(hit.group(1) or hit.group(2)).lower()]))
        return found, answered

    said, answered = served_claims()
    s.check("the public pages answer", len(answered) >= len(urls) - 2,
            detail=f"answered: {answered}")
    s.check("and they make real claims to check", len(said) >= 8,
            detail=f"{len(said)} — if they matched nothing the check below would "
                   "pass on an empty list, which is the shape this whole suite "
                   "exists to prevent")
    off = [f"{u} says {n}" for u, n in said if n != facts["count"]]
    s.check(f"every one of them says {facts['count_word']}", not off,
            detail="; ".join(off[:6]))

    s.section("The one exception is still an exception")
    stale = set(HISTORICAL) - seen_exceptions
    s.check("every listed exception still matches something", not stale,
            detail=f"{sorted(stale)} — a page rewritten so the sentence is "
                   "gone leaves an exception nobody can justify, and the next "
                   "person reads it as a rule")
    for (page, marker), why in HISTORICAL.items():
        s.check(f"{page}'s {marker} line is history, not a claim", bool(why),
                detail=why)

    s.section("And the pages follow the day another room goes on sale")
    # The point of the whole suite, proven by doing it: a room is put on sale
    # and every claim on every page must move with it. Taken away again --
    # the rooms are live data.
    conn.execute(
        """INSERT INTO rooms (name, export_token, active, max_occupancy,
                   price_per_night, sort_order)
           VALUES ('zz one more', 'zz-one-more-token', 1, 2, 300.0, 98)""")
    conn.commit()
    try:
        more = m.house_room_facts(conn)
        said_more, _ = served_claims()
    finally:
        conn.execute("DELETE FROM rooms WHERE export_token = 'zz-one-more-token'")
        conn.commit()
    s.check("the house counts it", more["count"] == facts["count"] + 1, detail=str(more))
    s.check("and every claim on the pages now says so",
            len(said_more) >= 8 and all(n == more["count"] for _u, n in said_more),
            detail="; ".join(f"{u} says {n}" for u, n in said_more
                             if n != more["count"])[:300] or f"{len(said_more)} claim(s)")
    s.check("and they go back when it comes off sale",
            m.house_room_facts(conn) == facts
            and all(n == facts["count"] for _u, n in served_claims()[0]))

    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
