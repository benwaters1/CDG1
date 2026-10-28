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

# The last commit before the image layout work touched a public page.
COPY_BASE = "ae0ee6c"

# Words the brief adds, each only inside its own element.
ALLOWED = {
    "g-car__n": re.compile(r"\d+ / \d+"),
    "g-seeall__b": re.compile(r"See all( \d+)? photos"),
}


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
        cls = (dict(attrs).get("class") or "").split()
        allow = next((pat for name, pat in ALLOWED.items() if name in cls), None)
        self._add(" ")           # a tag is a word break, as on screen
        self.stack.append([tag, allow, []])
        if tag in self.SKIP:
            self.skip += 1
        if tag == "figcaption":
            self.cap += 1
            if self.cap == 1:
                self._capbuf = []

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        while self.stack:
            t, allow, own = self.stack.pop()
            if t in self.SKIP:
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


def words_of(html):
    """(the words in order, the captions as a bag, any words added outside the rule)."""
    p = _Words()
    p.feed(html)
    p.close()
    return " ".join("".join(p.text).split()), Counter(p.caps), p.stray


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
            s.check(f"{path}: the same words, in the same order", text_now == text_then,
                    detail=_first_difference(text_now, text_then))
            new_caps = caps_now - caps_then
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
        s.check("sets the counter aside", not stray, detail=str(stray))
        _, _, stray = words_of('<p>x</p><button class="g-seeall__b">Book now</button>')
        s.check("but not new words dressed as one", stray == ["Book now"], detail=str(stray))
        # The guard must be able to fail: one word changed is a difference.
        a, _, _ = words_of("<p>The gingerbread château</p>")
        b, _, _ = words_of("<p>The gingerbread chateau</p>")
        s.check("one changed letter is a difference", a != b)
    finally:
        m.SITE_IS_LIVE = was_live
        shutil.rmtree(tmp, ignore_errors=True)
    return s


if __name__ == "__main__":
    print(run().report())
