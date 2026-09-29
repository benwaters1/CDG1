"""A heading down the side of a staff table sits where a cell would.

<th scope="row"> is how a screen reader hears "Balance due" and the figure
beside it as one fact rather than two loose cells, which is why
test_accessibility holds the statements to it. A totals row is labelled with
a th for the same reason. style.css styled the header row and the cells and
nothing else, so every one of these took the browser's defaults: centred,
bold, and none of a cell's padding or rule. Nothing errored and every page
rendered. The first column simply sat 14px off the heading above it, the line
under each row stopped short of it, and in eighteen totals rows the figures
sat off the columns they add up, with no rule above them at all. Forty-odd
headings had been put right one at a time with style="text-align:left;",
which is what a missing rule gets you: the same fix, over and over, where
nobody can see it.

ASKED OF THE CASCADE, NOT OF A SPELLING. A check that looks for the selector
passes on the day a later rule undoes it -- which is how the variant classes
in test_design were lost -- and fails on the day somebody writes the same
rule another way. So this resolves the stylesheet the way a browser does, for
a row heading and the cell beside it, at desk width and at phone width, and
compares what comes out. It models the CSS this stylesheet actually uses
(descendant and child selectors, classes, attributes, :not, the structural
pseudo-classes, one level of @media) and FAILS rather than guesses on
anything else that could reach a table cell, so a rule it cannot read is
never a rule it silently leaves out.

AND ONE PLACE THAT DECIDES. Once the stylesheet puts every heading on the
left, an inline text-align:left on a th is a copy of the old workaround, and
copies are how forty of them spread. None is left on a staff table.

The public pages are not in this. They are drawn with gudanes.css alone and
arrive from the design side as whole files.
"""
import os
import re

from _harness import Suite
import _harness

ROOT = _harness.ROOT
CSS_PATH = os.path.join(ROOT, "static", "style.css")
TEMPLATES = os.path.join(ROOT, "templates")

DESK = {"name": "desk", "width": 1280, "pointer": "fine", "hover": "hover"}
PHONE = {"name": "phone", "width": 375, "pointer": "coarse", "hover": "none"}
SIDES = ("top", "right", "bottom", "left")


class _Unread(Exception):
    """A piece of CSS this model does not evaluate."""


# ---------------------------------------------------------------------------
# The stylesheet as rules.

def _split(text):
    """Split on the commas that are not inside brackets or parentheses."""
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [x.strip() for x in out if x.strip()]


def parse(css):
    """Every style rule as (media, selector group, body, order).

    @media is followed one level deep, which is all this stylesheet uses;
    a query inside a query is kept as unreadable rather than guessed at.
    Other at-rules are passed over whole.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []

    def close(i):
        depth = 0
        for j in range(i, len(css)):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if not depth:
                    return j
        raise ValueError("a block opened near character %d is never closed" % i)

    def walk(start, stop, media):
        k = start
        while True:
            i = css.find("{", k, stop)
            if i < 0:
                return
            prelude = css[k:i]
            prelude = prelude[prelude.rfind(";") + 1:].strip()
            j = close(i)
            if prelude.startswith("@media"):
                walk(i + 1, j, prelude[6:].strip() if media is None else "(nested)")
            elif not prelude.startswith("@"):
                out.append((media, prelude, css[i + 1:j], len(out)))
            k = j + 1

    walk(0, len(css), None)
    return out


def _media(cond, env):
    """True, False, or None for a query this model cannot evaluate."""
    unknown = False
    for alt in _split(cond.lower()):
        verdict = True
        for part in re.split(r"\s+and\s+", alt):
            part = part.strip()
            if part in ("all", "screen", "only screen"):
                continue
            if part == "print":
                verdict = False
                break
            m = re.fullmatch(r"\(\s*(min|max)-width\s*:\s*([\d.]+)(px|rem|em)\s*\)", part)
            if m:
                px = float(m.group(2)) * (1 if m.group(3) == "px" else 16)
                ok = env["width"] <= px if m.group(1) == "max" else env["width"] >= px
                if not ok:
                    verdict = False
                    break
                continue
            m = re.fullmatch(r"\(\s*(pointer|hover)\s*:\s*([a-z]+)\s*\)", part)
            if m:
                if env[m.group(1)] != m.group(2):
                    verdict = False
                    break
                continue
            verdict = None
            break
        if verdict:
            return True
        if verdict is None:
            unknown = True
    return None if unknown else False


def _declarations(body):
    """[(property, value, important)], shorthands spelled out per side."""
    out = []
    for decl in re.split(r";(?![^(]*\))", body):
        prop, colon, value = decl.partition(":")
        prop, value = prop.strip().lower(), value.strip()
        if not colon or not prop:
            continue
        important = "!important" in value
        value = value.replace("!important", "").strip()
        if prop in ("padding", "margin"):
            v = value.split()
            if len(v) == 1:
                v = v * 4
            elif len(v) == 2:
                v = [v[0], v[1], v[0], v[1]]
            elif len(v) == 3:
                v = [v[0], v[1], v[2], v[1]]
            out += [("%s-%s" % (prop, side), x, important) for side, x in zip(SIDES, v[:4])]
        elif prop == "border":
            out += [("border-%s" % side, value, important) for side in SIDES]
        elif prop == "font":
            w = re.search(r"(?:^|\s)([1-9]00|bold|normal)(?=\s)", " " + value + " ")
            if w:
                out.append(("font-weight", w.group(1), important))
        else:
            out.append((prop, value, important))
    return out


# ---------------------------------------------------------------------------
# Selectors against a table cell.

class El:
    """One element on the way down to a cell: enough of it to match on."""

    def __init__(self, tag, cls="", attrs=None, first=True, last=True):
        self.tag = tag
        self.cls = set(cls.split())
        self.attrs = dict(attrs or {})
        if cls:
            self.attrs["class"] = cls
        self.first_child = self.first_of_type = first
        self.last_child = self.last_of_type = last


_PART = re.compile(r"\*|[a-zA-Z][\w-]*|\.[\w-]+|#[\w-]+|\[[^\]]*\]"
                   r"|::?[\w-]+(?:\((?:[^()]|\([^()]*\))*\))?")
_STATES = {"hover", "focus", "focus-visible", "focus-within", "active",
           "visited", "link", "checked", "disabled", "enabled", "target"}
_OLD_PSEUDO_ELEMENTS = {"before", "after", "first-line", "first-letter"}


def _parts(compound):
    parts, i = [], 0
    while i < len(compound):
        m = _PART.match(compound, i)
        if not m:
            raise _Unread(compound)
        parts.append(m.group(0))
        i = m.end()
    return parts


def _steps(selector):
    """[(combinator before it, compound)], left to right."""
    saved = []

    def keep(m):
        saved.append(m.group(0))
        return "\0%d\0" % (len(saved) - 1)

    s, prev = selector.strip(), None
    while prev != s:
        prev, s = s, re.sub(r"\[[^\[\]]*\]|\([^()]*\)", keep, s)
    s = re.sub(r"\s*([>+~])\s*", r" \1 ", s)
    out, pending = [], None
    for tok in s.split():
        if tok in (">", "+", "~"):
            pending = tok
            continue
        while "\0" in tok:
            tok = re.sub(r"\0(\d+)\0", lambda m: saved[int(m.group(1))], tok)
        out.append((pending, tok))
        pending = " "
    return out


def _simple(part, el):
    if part == "*":
        return True
    if part[0].isalpha():
        return el.tag == part.lower()
    if part[0] == ".":
        return part[1:] in el.cls
    if part[0] == "#":
        return el.attrs.get("id") == part[1:]
    if part[0] == "[":
        m = re.fullmatch(r'\[\s*([\w-]+)\s*(?:(=|~=)\s*("?)([^"\]]*)\3)?\s*\]', part)
        if not m:
            raise _Unread(part)
        have = el.attrs.get(m.group(1))
        if have is None or m.group(2) is None:
            return have is not None
        return have == m.group(4) if m.group(2) == "=" else m.group(4) in have.split()
    if part.startswith("::"):
        return False                    # another box, not the cell
    name, _, arg = part[1:].partition("(")
    if name in _OLD_PSEUDO_ELEMENTS or name in _STATES:
        return False                    # the cell at rest is what is compared
    if name == "root":
        return el.tag == "html"
    if name in ("first-child", "last-child", "first-of-type", "last-of-type"):
        return getattr(el, name.replace("-", "_"))
    if name == "not":
        return not any(_compound(x, el) for x in _split(arg[:-1]))
    raise _Unread(part)


def _compound(compound, el):
    # Pseudo-classes last, so one this model cannot read only matters when
    # everything else about the selector already reaches the element.
    for part in sorted(_parts(compound), key=lambda p: p.startswith(":")):
        if not _simple(part, el):
            return False
    return True


def matches(selector, path):
    steps = _steps(selector)

    def at(si, pi):
        comb, comp = steps[si]
        if not _compound(comp, path[pi]):
            return False
        if si == 0:
            return True
        if comb == ">":
            return pi > 0 and at(si - 1, pi - 1)
        if comb == " ":
            return any(at(si - 1, pj) for pj in range(pi - 1, -1, -1))
        raise _Unread("the %r combinator" % comb)

    return at(len(steps) - 1, len(path) - 1)


def specificity(selector):
    a = b = c = 0
    for _comb, comp in _steps(selector):
        for p in _parts(comp):
            if p == "*":
                continue
            if p[0].isalpha() or p.startswith("::"):
                c += 1
            elif p[0] == "#":
                a += 1
            elif p[0] == ":":
                name, _, arg = p[1:].partition("(")
                if name in _OLD_PSEUDO_ELEMENTS:
                    c += 1
                elif name in ("not", "is", "has"):
                    inner = max(specificity(x) for x in _split(arg[:-1]))
                    a, b, c = a + inner[0], b + inner[1], c + inner[2]
                elif name != "where":
                    b += 1
            else:
                b += 1
    return (a, b, c)


# ---------------------------------------------------------------------------
# The cascade, for the handful of properties that decide how a cell sits.

# What Chrome's own stylesheet gives table parts, for what is compared here.
UA = {
    "th": {"text-align": "center", "font-weight": "700", "vertical-align": "inherit",
           **{"padding-" + s: "1px" for s in SIDES}},
    "td": {"vertical-align": "inherit", **{"padding-" + s: "1px" for s in SIDES}},
    "tr": {"vertical-align": "inherit"},
    "thead": {"vertical-align": "middle"}, "tbody": {"vertical-align": "middle"},
    "tfoot": {"vertical-align": "middle"},
}
INHERITED = {"text-align", "font-weight"}


def _initial(prop):
    if prop.startswith("padding-"):
        return "0"
    if prop.startswith("border-"):
        return "none"
    return {"text-align": "left", "font-weight": "400",
            "vertical-align": "baseline"}.get(prop, "")


def _norm(prop, v):
    v = " ".join(str(v).lower().split())
    if prop.startswith("border-"):
        return "none" if v.split()[:1] in (["none"], ["0"], ["0px"], ["hidden"]) else v
    if prop.startswith("padding-"):
        return "0" if v in ("0", "0px") else v
    if prop == "font-weight":
        return {"normal": "400", "bold": "700"}.get(v, v)
    if prop == "text-align":
        return {"start": "left", "-internal-center": "center"}.get(v, v)
    return v


def _sig(path):
    """What a path IS, for the memo. Not id(): path[:-1] is a new list every
    time, and a freed list's id is handed to the next one, which would give
    one ancestor another's answer."""
    return tuple((e.tag, tuple(sorted(e.attrs.items())), e.first_child, e.last_child)
                 for e in path)


class Cascade:
    def __init__(self, css):
        self.rules = parse(css)
        self.unread = []
        self._declared, self._value = {}, {}

    def declared(self, path, env):
        key = (_sig(path), env["name"])
        if key in self._declared:
            return self._declared[key]
        best = {}
        for media, group, body, order in self.rules:
            applies = True if media is None else _media(media, env)
            if applies is False:
                continue
            for sel in _split(group):
                try:
                    hit = matches(sel, path)
                    spec = specificity(sel) if hit else None
                except _Unread as e:
                    note = "%s (%s)" % (sel, e)
                    if note not in self.unread:
                        self.unread.append(note)
                    continue
                if not hit:
                    continue
                if applies is None:
                    note = "@media %s { %s }" % (media, sel)
                    if note not in self.unread:
                        self.unread.append(note)
                    continue
                for prop, value, important in _declarations(body):
                    rank = (important, spec, order)
                    if prop not in best or rank >= best[prop][0]:
                        best[prop] = (rank, value)
        self._declared[key] = {p: v for p, (_r, v) in best.items()}
        return self._declared[key]

    def value(self, path, prop, env):
        key = (_sig(path), prop, env["name"])
        if key in self._value:
            return self._value[key]
        got = self.declared(path, env).get(prop)
        if got is None:
            got = UA.get(path[-1].tag, {}).get(prop)
            if got is None and prop in INHERITED:
                got = "inherit"
        if got == "inherit":
            got = self.value(path[:-1], prop, env) if len(path) > 1 else _initial(prop)
        elif got is None:
            got = _initial(prop)
        self._value[key] = _norm(prop, got)
        return self._value[key]


def _cell(section, tag, cls="", row="middle", pos="first", row_cls="", attrs=None):
    """A cell in a staff page, from the document down."""
    row_first, row_last = {"only": (True, True), "first": (True, False),
                           "middle": (False, False), "last": (False, True)}[row]
    first, last = {"first": (True, False), "middle": (False, False),
                   "last": (False, True)}[pos]
    return [El("html"), El("body", "staff-shell"), El("main", "page"),
            El("div", "table-wrap"), El("table", "data-table"),
            El(section, first=section == "thead", last=section == "tfoot"),
            El("tr", row_cls, first=row_first, last=row_last),
            El(tag, cls, attrs, first=first, last=last)]


PATHS = {
    "heading": _cell("tbody", "th", attrs={"scope": "row"}),
    "cell": _cell("tbody", "td", pos="middle"),
    "heading, last row": _cell("tbody", "th", row="last", attrs={"scope": "row"}),
    "cell, last row": _cell("tbody", "td", row="last", pos="middle"),
    "total heading": _cell("tbody", "th", row_cls="total", attrs={"scope": "row"}),
    "total cell": _cell("tbody", "td", row_cls="total", pos="middle"),
    # A foot of two rows (night margin has one), then its last row.
    "foot label": _cell("tfoot", "th", row="first"),
    "foot cell": _cell("tfoot", "td", row="first", pos="middle"),
    "foot label, last row": _cell("tfoot", "th", row="last"),
    "foot cell, last row": _cell("tfoot", "td", row="last", pos="middle"),
    "foot figure": _cell("tfoot", "th", cls="num", row="last", pos="last"),
    "column heading": _cell("thead", "th", row="only"),
}


def judgements(css):
    """[(question, answer, detail)] for one stylesheet.

    Kept apart from run() so the same questions can be put to a stylesheet
    that is known to be wrong, and seen to come out wrong.
    """
    try:
        c = Cascade(css)
    except ValueError as e:
        return [("the stylesheet can be read at all", False, str(e))]

    def v(name, prop, env=DESK):
        return c.value(PATHS[name], prop, env)

    def box(name, env=DESK):
        return tuple(v(name, "padding-" + s, env) for s in SIDES)

    def weight(name):
        w = v(name, "font-weight")
        return int(w) if w.isdigit() else None

    out = []

    def ask(question, ok, detail):
        out.append((question, bool(ok), detail))

    ask("a row heading sits on the left, not centred by the browser",
        v("heading", "text-align") == "left",
        "text-align resolves to %r" % v("heading", "text-align"))
    ask("at a weight between a cell's and a header's, not the browser's bold",
        weight("heading") is not None and 500 <= weight("heading") <= 600,
        "font-weight resolves to %r" % v("heading", "font-weight"))
    ask("with a cell's padding on every side",
        box("heading") == box("cell") and box("cell") != ("1px",) * 4,
        "heading %s, cell %s" % (box("heading"), box("cell")))
    ask("the line under the row runs beneath the heading too",
        v("heading", "border-bottom") == v("cell", "border-bottom") != "none",
        "heading %r, cell %r" % (v("heading", "border-bottom"), v("cell", "border-bottom")))
    ask("and it is top-aligned like the cells beside it",
        v("heading", "vertical-align") == v("cell", "vertical-align"),
        "heading %r, cell %r" % (v("heading", "vertical-align"), v("cell", "vertical-align")))
    ask("the last row has no rule under its heading, as under its cells",
        v("heading, last row", "border-bottom") == v("cell, last row", "border-bottom"),
        "heading %r, cell %r" % (v("heading, last row", "border-bottom"),
                                 v("cell, last row", "border-bottom")))

    ask("a totals label carries the total's weight and the rule above it",
        v("foot label", "font-weight") == v("foot cell", "font-weight")
        and v("foot label", "border-top") == v("foot cell", "border-top") != "none",
        "label %r / %r, cell %r / %r" % (
            v("foot label", "font-weight"), v("foot label", "border-top"),
            v("foot cell", "font-weight"), v("foot cell", "border-top")))
    ask("in a cell's box, with the same rule under it",
        box("foot label") == box("foot cell")
        and v("foot label", "border-bottom") == v("foot cell", "border-bottom")
        and v("foot label, last row", "border-bottom") == v("foot cell, last row", "border-bottom"),
        "label %s %r/%r, cell %s %r/%r" % (
            box("foot label"), v("foot label", "border-bottom"),
            v("foot label, last row", "border-bottom"), box("foot cell"),
            v("foot cell", "border-bottom"), v("foot cell, last row", "border-bottom")))
    ask("and it stays on the left, where the headings above it start",
        v("foot label", "text-align") == "left",
        "text-align resolves to %r" % v("foot label", "text-align"))
    ask("a figure in a totals row lines up with the column it adds up",
        v("foot figure", "text-align") == "right" and box("foot figure") == box("foot cell"),
        "text-align %r, padding %s against a cell's %s" % (
            v("foot figure", "text-align"), box("foot figure"), box("foot cell")))
    ask("a heading on a row marked as a total is treated as that total",
        v("total heading", "font-weight") == v("total cell", "font-weight")
        and v("total heading", "border-top") == v("total cell", "border-top"),
        "heading %r / %r, cell %r / %r" % (
            v("total heading", "font-weight"), v("total heading", "border-top"),
            v("total cell", "font-weight"), v("total cell", "border-top")))
    ask("the header row still reads as before",
        v("column heading", "text-align") == "left" and box("column heading") == box("cell"),
        "text-align %r, padding %s" % (v("column heading", "text-align"), box("column heading")))

    ask("on a phone the heading narrows with the cells",
        box("heading", PHONE) == box("cell", PHONE) and box("cell", PHONE) != box("cell"),
        "heading %s, cell %s at phone width (cell %s at desk width)" % (
            box("heading", PHONE), box("cell", PHONE), box("cell")))
    ask("and so does a totals label",
        box("foot label", PHONE) == box("foot cell", PHONE),
        "label %s, cell %s at phone width" % (box("foot label", PHONE), box("foot cell", PHONE)))

    ask("every rule that could reach a table cell was read",
        not c.unread,
        "cannot evaluate: %s" % "; ".join(c.unread[:3]))
    return out


# ---------------------------------------------------------------------------
# The templates.

def _public_names():
    """Public pages, and the partials only public pages include."""
    srcs = {n: open(os.path.join(TEMPLATES, n), encoding="utf-8").read()
            for n in os.listdir(TEMPLATES) if n.endswith(".html")}
    public = {n for n, s in srcs.items()
              if 'extends "public_base.html"' in s or n == "public_base.html"}
    users = {}
    for n, s in srcs.items():
        for inc in re.findall(r'\{%-?\s*(?:include|import|from)\s+"([^"]+)"', s):
            users.setdefault(inc, set()).add(n)
    grew = True
    while grew:
        grew = False
        for n in srcs:
            if n not in public and n.startswith("_") and users.get(n) and users[n] <= public:
                public.add(n)
                grew = True
    return srcs, public


_TABLE = re.compile(r'<table\b[^>]*\bclass="[^"]*\bdata-table\b[^"]*"[^>]*>.*?</table>', re.S)
_TOKEN = re.compile(r"</?(thead|tbody|tfoot)\b[^>]*>|<th\b[^>]*>")


def headings(src):
    """(line, section, tag) for every th in every data table in one template."""
    out = []
    for table in _TABLE.finditer(src):
        section = "tbody"               # rows written without a section go here
        for tok in _TOKEN.finditer(table.group(0)):
            text = tok.group(0)
            if tok.group(1):
                section = tok.group(1) if not text.startswith("</") else "tbody"
                continue
            line = src.count("\n", 0, table.start() + tok.start()) + 1
            out.append((line, section, text))
    return out


def inline_left(tag):
    style = re.search(r'\bstyle="([^"]*)"', tag)
    return bool(style and re.search(r"text-align\s*:\s*(left|start)\b", style.group(1)))


def run():
    s = Suite("Headings down the side of a table")
    css = open(CSS_PATH, encoding="utf-8").read()

    s.section("A row heading, resolved the way a browser resolves it")
    for question, ok, detail in judgements(css):
        s.check(question, ok, detail=detail)

    s.section("And the questions can come out wrong")
    # Every question above, put to a stylesheet that is right, then to five
    # that are wrong in one way each. A question that passes the broken ones
    # is not asking anything -- and the cascade is the part most likely to
    # be wrong, so it is the part proved here.
    good = """
      .data-table thead th{ text-align:left; font-weight:600; padding:11px 14px; }
      .data-table td, .data-table tbody th, .data-table tfoot th{
        padding:11px 14px; border-bottom:1px solid #ddd; vertical-align:top; }
      .data-table tbody th, .data-table tfoot th{ text-align:left; font-weight:500; }
      .data-table tbody tr:last-child td, .data-table tbody tr:last-child th,
      .data-table tfoot tr:last-child td, .data-table tfoot tr:last-child th{ border-bottom:none; }
      .data-table .num{ text-align:right; }
      .data-table tfoot td, .data-table tfoot th,
      .data-table tr.total td, .data-table tr.total th{ font-weight:700; border-top:2px solid #ddd; }
      @media (max-width: 760px){
        .data-table thead th, .data-table td,
        .data-table tbody th, .data-table tfoot th{ padding:9px 10px; } }
    """
    verdicts = {q: ok for q, ok, _d in judgements(good)}
    s.check("a stylesheet that does it right passes every question",
            all(verdicts.values()),
            detail="failed on it: %s" % [q for q, ok in verdicts.items() if not ok])

    def fails(variant, *questions):
        got = {q: ok for q, ok, _d in judgements(variant)}
        return all(got.get(q) is False for q in questions) and sum(
            1 for ok in got.values() if not ok) == len(questions), got

    broken = [
        ("with no rule for headings at all, the browser's defaults are what they get",
         good.replace(", .data-table tbody th, .data-table tfoot th{\n        padding", "{\n        padding")
             .replace(".data-table tbody th, .data-table tfoot th{ text-align:left; font-weight:500; }", ""),
         ["a row heading sits on the left, not centred by the browser",
          "at a weight between a cell's and a header's, not the browser's bold",
          "with a cell's padding on every side",
          "the line under the row runs beneath the heading too",
          "and it is top-aligned like the cells beside it",
          "in a cell's box, with the same rule under it",
          "and it stays on the left, where the headings above it start",
          "a figure in a totals row lines up with the column it adds up"]),
        ("a later, weightier rule that centres them again is seen",
         good + ".data-table tbody tr th{ text-align:center; }",
         ["a row heading sits on the left, not centred by the browser"]),
        ("a phone rule that narrows the cells and not the headings is seen",
         good.replace(",\n        .data-table tbody th, .data-table tfoot th{ padding:9px 10px; }",
                      "{ padding:9px 10px; }"),
         ["on a phone the heading narrows with the cells", "and so does a totals label"]),
        ("a cell's padding changed on its own is seen",
         good.replace("vertical-align:top; }",
                      "vertical-align:top; }\n      .data-table td{ padding:12px 16px; }"),
         ["with a cell's padding on every side", "in a cell's box, with the same rule under it",
          "a figure in a totals row lines up with the column it adds up",
          "the header row still reads as before"]),
        ("a rule the model cannot read is reported, not skipped",
         good + ".data-table tr ~ tr th{ padding:0; }",
         ["every rule that could reach a table cell was read"]),
    ]
    for label, variant, questions in broken:
        ok, got = fails(variant, *questions)
        s.check(label, ok, detail="expected exactly %s to fail; failed: %s" % (
            questions, [q for q, v in got.items() if not v]))

    s.section("No heading in a staff table sets its own alignment")
    srcs, public = _public_names()
    seen, templates, offenders = 0, 0, []
    for name in sorted(srcs):
        if name in public:
            continue
        found = headings(srcs[name])
        down_the_side = [h for h in found if h[1] != "thead"]
        seen += len(down_the_side)
        templates += bool(down_the_side)
        offenders += ["%s:%d" % (name, line) for line, _sec, tag in found if inline_left(tag)]
    # Counted, so a sweep that stopped finding tables would say so rather
    # than pass on nothing.
    s.check("the sweep reads %d headings down the side of %d staff templates"
            % (seen, templates), seen > 100 and templates > 30,
            detail="it found too few to be reading the templates at all")
    s.check("none carries an inline text-align:left the stylesheet already gives it",
            not offenders,
            detail="%d, e.g. %s -- the rule decides; a copy of the old workaround "
                   "is how forty of them spread" % (len(offenders), offenders[:4]))
    s.check("and the sweep can tell the difference",
            [inline_left(t) for _l, _s, t in headings(
                '<table class="data-table"><thead><tr><th style="text-align:left;">A</th></tr></thead>'
                '<tbody><tr><th scope="row" style="text-align: left">B</th>'
                '<th scope="row" style="font-weight:400;">C</th></tr></tbody></table>'
                '<table class="other"><tr><th style="text-align:left;">D</th></tr></table>')]
            == [True, True, False],
            detail="an inline left must be caught in the header and the body, a "
                   "different style must not, and a table that is not a data table "
                   "is not this rule's business")
    s.check("the public pages are left to their own stylesheet",
            "guest_statement.html" in public and "public_base.html" in public
            and "guest_full_statement.html" not in public,
            detail="the guest's bill is drawn with gudanes.css; the staff statement "
                   "is drawn with this one")
    return s


if __name__ == "__main__":
    print(run().report())
