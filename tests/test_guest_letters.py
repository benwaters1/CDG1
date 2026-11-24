"""Guests' own words from their thank-you letters, and never their names.

The owner, 24 November 2026 (BRIEF-guest-quotes-for-Claude-Code.md): lines from
the letters guests wrote after their stay go on the home page, the retreats page
and the weddings page. Quoted verbatim, cut only at a sentence. NO NAMES: each is
signed with the season and the year, nothing that could identify the guest.

Held here:
  - each line is on its page, with its signature beside it;
  - none of the names in the letters is on any public page, nor in a template's
    words (a template's photograph addresses are the old CDN's file names, which
    the house swaps for its own copies at the response, so they are left out);
  - the weddings questions name Les Gites de Beille as the card above does, never
    Pays de Beille, and the note under the places to stay says the house arranges
    them and the shuttle; the photoshoot page keeps its own note.
"""
import glob
import html as htmllib
import os
import re

from _harness import Suite, visible_text
import _harness

m = _harness.m
ROOT = _harness.ROOT

NAMES = ("Laura", "Millie", "Darcie", "Cody", "Rebecca", "Ken")

LETTERS = {
    "/": [
        ("Several times every day, I witnessed magic being performed in the Chateau kitchen, at the table, "
         "and the hours in between.", "Workshop guest, summer 2024"),
        ("We truly felt like royalty, but more importantly, like family.", "Workshop guest, autumn 2023"),
        ("Nothing we ate in Paris compared to the meals and desserts.", "Workshop guest, autumn 2023"),
    ],
    "/workshops": [
        ("From stunning arrival to tearful farewell, I felt I was a member of your family’s elegant household, "
         "an honored guest.", "Workshop guest, summer 2024"),
        ("We had an absolutely wonderful experience and have told so many about how much magic the Chateau "
         "boasts. Can’t wait to come back again.", "Workshop guest, autumn 2023"),
        ("The château is truly in a magical location.", "Workshop guest, autumn 2023"),
    ],
    "/events/weddings": [
        # 24 Nov 2026 (owner, of "We are still dreaming of being at the Chateau with everyone.": "this doesnt seem
        # like a testimonial"): another line from the same couple's letter.
        ("I’ve found a lot of delight watching virtually as subsequent guests enjoyed the chateau, knowing how "
         "magical the experience is to be there with you all.", "Married at the château, 2024"),
    ],
}

PUBLIC = ("/", "/workshops", "/events", "/events/weddings", "/events/private", "/events/photoshoots", "/book",
          "/facilities", "/whats-on", "/gallery", "/contact", "/press", "/history", "/restoration")

WEDDINGS_NOTE = "We arrange these rooms for you, and the shuttle between them and the château."
BOOKED_DIRECTLY = "Each is booked directly. Ask us, and we will gladly tell you which would suit your party."


def flat(text):
    return re.sub(r"\s+", " ", htmllib.unescape(text)).strip()


def signed(page, quote, by):
    """The quote and its signature in the one element that holds them both."""
    for block, _tag in re.findall(r"(<(figure|blockquote)\b.*?</\2>)", page, re.S):
        words = flat(visible_text(block))
        if quote in words and by in words:
            return True
    return False


def names_in(text):
    return [n for n in NAMES if re.search(r"\b%s\b" % n, text)]


def template_words(src):
    """A template's text with its comments and every address taken out: what it could ever say."""
    src = re.sub(r"\{#.*?#\}", " ", src, flags=re.S)
    src = re.sub(r"<!--.*?-->", " ", src, flags=re.S)
    src = re.sub(r"https?://[^\s'\"<>)]+", " ", src)
    src = re.sub(r"filename=['\"][^'\"]*['\"]", " ", src)
    return src


def run():
    s = Suite("Guests' own words, and never their names")
    client = m.app.test_client()
    pages = {p: client.get(p) for p in set(PUBLIC) | set(LETTERS)}

    s.section("Each line on its page, signed with the season and the year")
    for path, lines in LETTERS.items():
        page = pages[path].get_data(as_text=True)
        s.check(f"{path} answers", pages[path].status_code == 200, detail=str(pages[path].status_code))
        for quote, by in lines:
            s.check(f"{path}: “{quote[:48]}…” as {by}", signed(page, quote, by),
                    detail=flat(visible_text(page))[:0] or "not found together")
    # Negative control: a line not on the page is not found.
    s.check("a line that is not there is not found",
            not signed(pages["/"].get_data(as_text=True), "We never wrote this line.", "Workshop guest, summer 2024"))

    s.section("No names")
    said = {}
    for path, r in pages.items():
        found = names_in(flat(visible_text(r.get_data(as_text=True))))
        if found:
            said[path] = found
    s.check("no public page says a name from the letters", not said, detail=str(said))
    in_src = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "templates", "**", "*.html"), recursive=True)):
        found = names_in(template_words(open(f, encoding="utf-8").read()))
        if found:
            in_src[os.path.relpath(f, ROOT)] = found
    s.check("no template's words carry one", not in_src, detail=str(in_src))
    # Negative controls: the check sees a name in a page's words and in a template's, and lets an address pass.
    s.check("the check sees a name in a page's words", names_in("With love, Darcie and Cody") == ["Darcie", "Cody"])
    s.check("and in a template's words, comments aside",
            names_in(template_words("{# Rebecca #}<p>Thank you, Laura</p>")) == ["Laura"])
    s.check("and lets the old CDN's file name pass",
            names_in(template_words('<img src="https://cdn.example/Rebecca%26Ken_x.jpg">')) == [])

    s.section("The weddings page: the places to stay, said one way")
    wed = flat(visible_text(pages["/events/weddings"].get_data(as_text=True)))
    s.check("the questions name Les Gîtes de Beille", "three places close by: Les Gîtes de Beille" in wed)
    s.check("and never Pays de Beille", "Pays de Beille" not in wed)
    s.check("the note under the places says we arrange them and the shuttle", WEDDINGS_NOTE in wed)
    s.check("and not that each is booked directly", BOOKED_DIRECTLY not in wed)
    shoot = flat(visible_text(pages["/events/photoshoots"].get_data(as_text=True)))
    s.check("the photoshoot page keeps its own note", BOOKED_DIRECTLY in shoot and WEDDINGS_NOTE not in shoot)
    return s


if __name__ == "__main__":
    print(run().report())
