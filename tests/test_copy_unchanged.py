"""The words on the public pages are the words they had (image layout brief, 27 Oct 2026).

The image layout work moves, sizes and crops photographs on every public page
and is not allowed to change a single word. "Not allowed" means nothing unless
something reads the words, so this does: every page in the sitemap is rendered
twice in the same run, once with the templates as they are and once with the
templates as they stood at COPY_BASE, and the visible text must match.

Twice in the same run, against the same database, rather than against a saved
transcript: the pages carry live prices, dates and places left, and a saved
transcript of those would go red the first time the owner changed a price.

What is compared:

  - The words outside figure captions, in order, exactly.
  - The figure captions as a bag. They travel with their photographs, so a
    photograph moved down the page moves its caption, and one sent to the
    lightbox takes its caption with it (they are hidden on the site anyway,
    owner 15 Oct). But no caption may appear that was not there before.

The only words a page may gain are the two the brief itself asks for, and only
in their own elements: a slider's counter ("3 / 8") and "See all 23 photos".

COPY CHANGED ON PURPOSE? Then this is doing its job. Look at what it lists,
and if every difference is meant, move COPY_BASE to the commit that made them.
"""
import html as htmlmod
import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from collections import Counter
from html.parser import HTMLParser

import jinja2

from _harness import Suite, ROOT
import _harness

m = _harness.m

# The words as they last stood on purpose. Was ae0ee6c, the last commit before
# the image layout work touched a public page; moved 9 Nov 2026 to the commit
# that rewrote the retreat days at the owner's request (approved 9 Nov, after
# a second read-aloud pass), which also changed
# the Noël card line and three opening paragraphs. Every difference it listed
# was one of those five pages: /workshops and the four retreat pages.
# 9 Nov, later: moved to the merge with main at 0630e3d (e9b2f1a), so the base
# holds main's own words as well as the retreat text. Checked against main
# itself first: the only pages that differ from main are the five above.
# 9 Nov, evening: moved again, to the commit that finished the owner's second review
# of the site (BRIEF-site-review-2-9-nov.md): the pages a third of their length,
# "Retreats" in the menu, "château" for "house", the hero's chosen line, and no dish,
# drink or artisan on a retreat's day. Every difference against e9b2f1a was read and
# is that brief's, on top of the retreat days above.
# 10 Nov: moved to 36fd627, the owner's answers of that day (the Christmas market on
# Noël, Cooking's opening without dishes); it listed /workshops, /workshops/3
# and /workshops/4, and nothing else.
# 10 Nov, later: moved to c7fb234, the owner's bed-and-breakfast wording in the comparisons ("make it clear
# that one is a b&b"); it listed /, /book, /workshops and the five room pages, and nothing else.
# 10 Nov, evening: moved to be0b84c, the merge of the second review with main's two
# commits of that day. Against 2bbe116 it listed /, /book, /workshops, the five room
# pages, /workshops/3 and /workshops/4, and nothing else: the bed-and-breakfast wording
# (with the owner's "on site" for "in the house"), Cooking's opening without dishes,
# and Noël's Christmas market. Each was read.
# 10 Nov, later: 96264b8 put Bastille Day and the Fête de la Musique on the retreat days
# (/workshops/4 and /workshops/6); merged with b6b4ad8 below, the base moves to the merge.
# 10 Nov, later still: moved to b6b4ad8, the same comparisons made succinct again (room and breakfast, lunch and
# dinner unavailable, the restaurants nearby); it listed the same eight pages, and nothing else.
# and then to aa34fe9, the merge of the two: against b6b4ad8 it listed /workshops/4
# and /workshops/6 only, the retreat evenings above.
# 10 Nov: then ecacf9b, Noël's market the village's own, a walk away; it listed
# /workshops/3 (the Noël page) only.
# 10 Nov, again: a8b7c05, the retreats' "every meal, and drinks included" (owner); it listed /, /book
# and /workshops, and nothing else. Then c6b0dda, the merge of the two: against a8b7c05 it listed
# /workshops/3 only, Noël's market above.
# 10 Nov, night: moved to 7df0813, the merge of the second review with all of the above.
# Against e912ba8 it listed /, /book, /workshops, the five room pages, /workshops/3, /4
# and /6, and nothing else: room and breakfast with the restaurants nearby, the village
# Christmas market, and the days split into day and evening. Each was read; the review's
# welcome glass, no artisan, no pool on Cooking and "on site" for "in the house" kept.
# 11 Nov: moved to dc5309b, Cooking in the Cuisine's "five spring days" (owner: "change to
# spring"); it listed /workshops and /workshops/4, and nothing else.
# 11 Nov: moved to 06ba88c, the bathrooms' section on /book (owner: "they are a real feature");
# it listed /book only.
# 11 Nov, later: moved to d1749d8, the bathrooms made a feature (owner: "make it beyyer"); it listed
# /book only.
# 11 Nov: moved to d0edfb2, the owner's "from" prices (weddings from €120,000 + VAT; a private retreat
# from €45,000 for four nights); it listed /workshops and /events/weddings, and nothing else.
# 11 Nov, evening: moved to 4c2827e, the owner's site-copy brief (the bathrooms as a feature, the
# retreat days in dot points, What Guests Say gilded); it listed /, /book, /facilities (What Is
# Included), the room pages and the four retreat pages, and nothing else.
# 12 Nov: moved to 9fda41b, the "from" prices taken off again (owner); it listed /workshops and
# /events/weddings, and nothing else.
# 12 Nov: the two moved together, to f0eb8dd, the merge that holds both (the brief's pages and the
# prices taken off).
# 12 Nov: moved to 524b5b9, one home per photograph drawn large (the second review, 3.3); it listed
# /restoration only, "The kitchen" for the dining room's line.
# 12 Nov: both moved to 42862c8, the merge that holds the site-copy brief and one home per photograph.
# 13 Nov: moved to c5506fa, the bathrooms back below the rooms with all the owner's own facts ("wheres all
# the stuff i said"); it listed /book only.
# 13 Nov: moved to 2f463c9, the room picker (Answer Two Questions) taken off /book; it listed /book only.
# 23 Nov: moved to 67e2599, how many a room sleeps as its own total (owner: "it sleeps up to 3 for the
# ground floor"); it listed /, /book and /book/1, "Sleeps 3, up to 2 adults" for "Sleeps 2", and nothing else.
# 23 Nov: moved to 68c905f, Les Bains du Couloubret off Things to Do Nearby (owner: "yes if its on there
# twice"); it listed /whats-on only.
# 23 Nov 2026: moved to 6c6e9da, the owner's website brief. Against 68c905f it listed /, /book,
# /book/1 to /book/5 (each room headed by its French title over its plain name, which
# the <title> of a room's page carries too) and /press (rebuilt, its captions shown on
# purpose), and nothing else. Each was read.
# 23 Nov 2026, later: moved to 310d694, the menu the owner asked to be easier to
# follow. Against 6c6e9da every page differed in one place only, the menu's
# Chateau group: The History before The Restoration, and the photograph's
# "Under restoration since 2013" gone with it. Read on every page listed.
# 23 Nov 2026, on main meanwhile: 1ad191a, the owner's chosen reviews, on purpose.
# Merged: the base is the merge, which holds both sets, each read on its own side.
# 23 Nov 2026, night: moved to 1d27227, the last of the Cowork bundle (retreat-questions.bundle) landed on main:
# the retreats first on the home page, the search bars, the type and colour passes, the menu's Check dates, the
# photoshoot page, and weddings and private events in the brochures' words. Against 4fd612f it listed /, /book,
# /workshops, /events/weddings, /events/private and /events/photoshoots, and nothing else: the bundle's pages.
# 23 Nov 2026, later: moved to cf9625d, "the house" made "the chateau" on the weddings, private events and photoshoot
# pages (the second review's word list), and "House parties" became "A party of friends". Against 1d27227 it listed
# /events/weddings, /events/private and /events/photoshoots, and nothing else. Was: "1d27227"
# 23 Nov 2026, evening: moved to 265c4d9, check-out at 11am (owner). Against 4fd612f it
# listed /book (Key Information's new "Arrival and departure" line) and /book/1 to
# /book/5 (the times above Pay & book), and nothing else. Each was read.
# 24 Nov 2026: merged with main at 9d00c13; the base is the merge. Against cf9625d it listed /book and
# /book/1 to /book/5, the check-out lines above, and nothing else (What's On's drawings add no words).
# 24 Nov 2026: moved to 6d92fc0, guests' own words from their thank-you letters (owner's brief): three on the home
# page, three on /workshops, one on the weddings page, each signed with the season and the year; the weddings questions
# say Les Gites de Beille and the note under its places to stay says we arrange them. Against cf9625d it listed /,
# /workshops and /events/weddings, and nothing else. Was: "cf9625d"
# 24 Nov 2026, later: merged with main at 96f5fe9; the base is the merge. Against 6d92fc0 it listed /book and
# /book/1 to /book/5 (check-out at 11am) and nothing else.
# 24 Nov 2026, evening: moved to 5b20f5a, the restoration page's own section for the painted beams and the frescoes
# (owner's brief: "yes, they are 16th century hand painted"), its sentence out of What the Walls Gave Back, and the
# timeline's 2018, 2019 and 2026. Against 96f5fe9 it listed /restoration (those words, and the three new captions) and
# nothing else. Was: "96f5fe9"
# 24 Nov 2026, night: moved to a4eb7a5, the same brief's sections 6 to 11: weddings seat eighty and are all inclusive,
# the wedding quote, the event pages most important first, /facilities ("On a retreat", cycling), and every description
# of a retreat ending open ("and much more"), the menu card's line on every page with it. Against 5b20f5a it listed the
# menu card on every page; with that line put back for one run, /, /workshops, /events, /events/weddings,
# /events/private, /facilities, the La vie de chateau card on /press, /story and /gallery, and the four retreats' own
# pages, each at one of those changes, and nothing else. Was: "5b20f5a"
# 24 Nov 2026, late: moved to f0e9feb, the retreats brief (/workshops made to sell, from 20d2ef0, 23 Nov) and the
# site review of 24 Nov on top of a4eb7a5: workshop and the full experience out of the footer, newsletter and every
# page that said them, lunch and dinner at the restaurants nearby, /story said once, /restaurant as The Table, the
# Queen Room's line, the facts above the price, What is open next by date, the What's On fold, the retreats' alt
# words and the wine in the lounge. Against a4eb7a5 it listed /, /book and /book/4 to /book/8, /contact, /events and
# its three pages (the footer), /facilities, /gallery, /history, /press, /restaurant, /restoration, /story, /terms
# (its description and the footer), /whats-on, /workshops and the four retreats' pages, and nothing else.
# Was: "a4eb7a5"
# 24 Nov 2026, late: moved to 46b6c34, the five branches merged in one (restoration 6 to 11, the retreats
# page and the old addresses, Occasions and Getting Here, the booking path, the owner home's clearings) and
# the fixes on top. Against a4eb7a5 it listed every public page, each first at the main menu's new What's On
# dropdown (In the Ariège, At Gudanes: the occasions brief), and the room pages' "With the Room" lines.
# Was: "a4eb7a5"
# 24 Nov 2026, later still: moved to 2d3c474, the room page a guest books from with its form first (owner: "too much
# scrolling" before the details): the room's words, its facts, the note on the walls and the gallery moved from above
# the form to below it. Against 46b6c34 it listed /book/1 to /book/5 and nothing else, each the same words in a new
# order (counted word for word, every room, with dates and without). Was: "46b6c34"
# 24 Nov 2026, night: moved to e272bf9, the site review of 24 Nov merged with main at 9a971f7. Against 2d3c474
# it listed the same pages as against a4eb7a5 above (the footer's label on every page, and each page the review
# changed) and nothing else. Was: "2d3c474"
# 25 Nov 2026: moved to e26d86d, BRIEF-private-retreat-and-images merged with main at bdd8e3c. Against e272bf9 it
# listed every page (the menu's "Private Retreats" where it said "Private"), /events/private (now a private retreat),
# /events/weddings (the JBL speakers), /contact (its topic "A private retreat"), /workshops (Groups lead to the
# private retreat) and /gallery (its three sections), which is that brief. Was: "e272bf9"
# 25 Nov 2026, later: moved to 89a5af6, BRIEF-occasions-in-the-retreats merged with main at f110776. Against
# e26d86d it listed /workshops/352 (Cooking in the Cuisine: day 2's heading and evening name L'Ariégeoise) and
# nothing else; the cards' "a night" and the search results' occasion line need a live occasion or a search,
# which this copy has neither of (test_stay_occasions holds them). Was: "e26d86d"
# 25 Nov 2026, evening: moved to 2f95473, BRIEF-itinerary-wording (the retreat days as an invitation, and Cooking
# in the Cuisine's classes). Against 89a5af6 it listed /events/private (Choose Your Days and the sample private
# retreat) and /workshops/351 to /workshops/354 (the four retreats' day-by-day), and nothing else. Was: "89a5af6"
COPY_BASE = "2f95473"

# Words the brief adds, each only inside its own element.
ALLOWED = {
    "g-car__n": re.compile(r"\d+ / \d+"),
    "g-seeall__b": re.compile(r"See all( \d+)? photos"),
    # A Wikimedia Commons photograph's licence asks for its credit to be seen.
    "g-credit": re.compile(r"Photo .{1,60}, CC BY(-SA)? \d\.\d"),
    # 9 Nov 2026 (owner): the house's address under the home page's hero, the
    # footer's own words, linked to the gates. test_house_address holds it.
    "g-where": re.compile(re.escape("%(street)s, %(locality)s, %(country)s" % m.HOUSE_PLACE)),
}


def _room_words():
    """Each room's own lines (owner, 9 Nov 2026: "room words is important"), exactly as
    _room_words.html writes them: the room page's g-room__words may hold one of these and nothing else."""
    path = os.path.join(ROOT, "templates", "_room_words.html")
    names = re.findall(r"^\s*'([^']+)': '", io.open(path, encoding="utf-8").read(), re.M)
    with m.app.app_context():
        module = m.app.jinja_env.get_template("_room_words.html").module
        return [" ".join(str(module.room_words(n)).split()) for n in names]


ALLOWED["g-room__words"] = re.compile("|".join(re.escape(w) for w in _room_words() if w))

# Words the owner has agreed may go, each named, and only these (28 Oct 2026:
# "drop the '…, in Pictures' headings as the brief says"). Taken out of the
# BEFORE text, so anything else that went is still a difference.
REMOVED = [
    re.compile(r"En images In pictures [^,]{1,80}, in Pictures "),
]


def _less_removed(text):
    for pattern in REMOVED:
        text = pattern.sub("", text)
    return text



class _Words(HTMLParser):
    """Visible words, with figure captions kept apart."""

    SKIP = {"script", "style", "template", "noscript"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []          # [tag, allowed pattern or None, its own words]
        self.skip = 0
        self.cap = 0
        self.text, self.caps = [], []
        self.extra, self.stray = [], []   # allowed words; words that broke the rule
        self._capbuf = []

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        found = dict(attrs)
        cls = (found.get("class") or "").split()
        # Not on screen: anything hidden, and a dialog nobody has opened (the
        # lightbox's close and arrows are drawn only once it opens).
        unseen = "hidden" in found or (tag == "dialog" and "open" not in found)
        allow = next((pat for name, pat in ALLOWED.items() if name in cls), None)
        self._add(" ")           # a tag is a word break, as on screen
        self.stack.append([tag, allow, [], unseen])
        if tag in self.SKIP or unseen:
            self.skip += 1
        if tag == "figcaption":
            self.cap += 1
            if self.cap == 1:
                self._capbuf = []

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        while self.stack:
            t, allow, own, unseen = self.stack.pop()
            if t in self.SKIP or unseen:
                self.skip -= 1
            if allow is not None:
                words = " ".join("".join(own).split())
                (self.extra if allow.fullmatch(words) else self.stray).append(words)
            if t == "figcaption":
                self.cap -= 1
                if self.cap == 0:
                    words = " ".join("".join(self._capbuf).split())
                    if words:
                        self.caps.append(words)
            if t == tag:
                break
        self._add(" ")

    def handle_data(self, data):
        self._add(data)

    def _add(self, data):
        if self.skip:
            return
        owner = next((f for f in reversed(self.stack) if f[1] is not None), None)
        if owner is not None:
            owner[2].append(data)
        elif self.cap:
            self._capbuf.append(data)
        else:
            self.text.append(data)


# A slider's arrows and a dialog's close, standing alone: controls, not words. The image layout
# swaps one kind of slider for another, and the old one's "›" going is not a change of copy.
GLYPHS = {"‹", "›", "×", "←", "→", "‹›"}


def words_of(html):
    """(the words in order, the captions as a bag, any words added outside the rule)."""
    p = _Words()
    p.feed(html)
    p.close()
    words = [w for w in "".join(p.text).split() if w not in GLYPHS]
    return " ".join(words), Counter(p.caps), p.stray


def every_caption(html):
    """Every figure caption in the page, on screen or behind a See all, where a photograph carries its words only as
    its description (alt) until the strip is opened."""
    found = (re.findall(r"(?is)<figcaption\b[^>]*>(.*?)</figcaption>", html)
             + re.findall(r'<img\b[^>]*\balt="([^"]+)"', html))
    return {" ".join(htmlmod.unescape(re.sub(r"<[^>]+>", " ", c)).split()) for c in found}


def _first_difference(a, b, span=70):
    """Where two long strings part company, with a little of each."""
    i = 0
    n = min(len(a), len(b))
    while i < n and a[i] == b[i]:
        i += 1
    lo = max(0, i - 25)
    return f"…{a[lo:i + span]!r}  was  …{b[lo:i + span]!r}"


def _baseline_templates(where):
    """The templates folder as it stood at COPY_BASE, written into `where`."""
    out = subprocess.run(["git", "archive", "--format=tar", COPY_BASE, "templates"],
                         cwd=ROOT, capture_output=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode("utf-8", "replace")[:200])
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tar:
        tar.extractall(where)
    return os.path.join(where, "templates")


def public_paths(client):
    """Every page the sitemap offers, as paths on this app."""
    body = client.get("/sitemap.xml").get_data(as_text=True)
    paths = []
    for loc in re.findall(r"<loc>([^<]+)</loc>", body):
        path = re.sub(r"^https?://[^/]+", "", loc.strip()) or "/"
        if path not in paths:
            paths.append(path)
    return paths


def render_all(client, paths):
    pages = {}
    for path in paths:
        r = client.get(path)
        pages[path] = (r.status_code, r.get_data(as_text=True))
    return pages


def run():
    s = Suite("Copy unchanged by the image layout")
    env = m.app.jinja_env
    client = m.app.test_client()
    was_live = m.SITE_IS_LIVE
    tmp = tempfile.mkdtemp(prefix="gudanes_copybase_")
    try:
        m.SITE_IS_LIVE = True          # the sitemap and the pages as the public sees them
        paths = public_paths(client)
        s.check("the sitemap lists the public pages", len(paths) >= 15,
                detail=f"{len(paths)}: {paths[:5]}")

        try:
            base_dir = _baseline_templates(tmp)
        except Exception as exc:        # no git, or COPY_BASE unknown: a failure, never a skip
            s.check(f"the templates at {COPY_BASE} can be read", False, detail=str(exc))
            return s

        now = render_all(client, paths)
        old_loader = env.loader
        try:
            env.loader = jinja2.FileSystemLoader(base_dir)
            env.cache.clear() if env.cache is not None else None
            then = render_all(client, paths)
        finally:
            env.loader = old_loader
            env.cache.clear() if env.cache is not None else None

        s.section(f"every page's words, against {COPY_BASE}")
        for path in paths:
            code_now, html_now = now[path]
            code_then, html_then = then[path]
            if code_then != 200:
                # A page that did not exist at COPY_BASE has nothing to compare.
                s.check(f"{path} loads", code_now == 200, detail=str(code_now))
                continue
            if code_now != 200:
                s.check(f"{path} still loads", False, detail=str(code_now))
                continue
            text_now, caps_now, stray = words_of(html_now)
            text_then, caps_then, _ = words_of(html_then)
            text_then = _less_removed(text_then)
            s.check(f"{path}: the same words, in the same order", text_now == text_then,
                    detail=_first_difference(text_now, text_then))
            # 23 Nov: a caption the page already carried behind its See all is not new words when a duplicate
            # photograph above it goes and it moves up into view (the gallery's four-poster, twice).
            new_caps = Counter({c: n for c, n in (caps_now - caps_then).items() if c not in every_caption(html_then)})
            s.check(f"{path}: no caption it did not have", not new_caps,
                    detail=str(list(new_caps)[:3]))
            s.check(f"{path}: a counter or See all says only that", not stray,
                    detail=str(stray[:3]))

        s.section("the reader itself")
        sample = ('<main><p>One <b>two</b></p><figure><img alt="x"><figcaption>A caption</figcaption></figure>'
                  '<span class="g-car__n">3 / 8</span><script>var hidden = 1</script><p>three</p></main>')
        text, caps, stray = words_of(sample)
        s.check("reads the words in order", text == "One two three", detail=repr(text))
        s.check("keeps captions apart", caps == Counter(["A caption"]), detail=str(caps))
        behind = every_caption('<figure><figcaption>Seen</figcaption></figure><div hidden><figure><figcaption>Behind '
                               '<b>See</b> all</figcaption></figure></div>')
        behind |= every_caption('<div hidden><img src="x.jpg" alt="Only an alt &amp; no caption"></div>')
        s.check("and knows the ones behind a See all too, a caption or a photograph's alt",
                behind == {"Seen", "Behind See all", "Only an alt & no caption"}, detail=str(behind))
        s.check("sets the counter aside", not stray, detail=str(stray))
        _, _, stray = words_of('<p>x</p><button class="g-seeall__b">Book now</button>')
        s.check("but not new words dressed as one", stray == ["Book now"], detail=str(stray))
        text, _, _ = words_of('<p>One</p><div hidden><p>gone</p></div><dialog><button>x</button></dialog>'
                              '<dialog open><p>two</p></dialog>')
        s.check("what is not on screen is not read", text == "One two", detail=repr(text))
        text, _, _ = words_of('<p>Room</p><button>›</button><p>One › two</p>')
        s.check("a slider's arrow standing alone is not a word", text == "Room One two", detail=repr(text))
        text, _, _ = words_of('<p>Dates→</p>')
        s.check("but an arrow inside a word is kept", text == "Dates→", detail=repr(text))
        # The guard must be able to fail: one word changed is a difference.
        a, _, _ = words_of("<p>The gingerbread château</p>")
        b, _, _ = words_of("<p>The gingerbread chateau</p>")
        s.check("one changed letter is a difference", a != b)
        s.check("the agreed removal is taken out of the before text",
                _less_removed("Dates En images In pictures Noël at Gudanes, in Pictures Choose")
                == "Dates Choose")
        s.check("and nothing else is",
                _less_removed("Dates In pictures, Choose") == "Dates In pictures, Choose")
    finally:
        m.SITE_IS_LIVE = was_live
        shutil.rmtree(tmp, ignore_errors=True)
    return s


if __name__ == "__main__":
    print(run().report())
