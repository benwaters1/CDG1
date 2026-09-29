"""Every box a member of staff types into is drawn by the house, not the browser.

style.css gives a text box its padding, its border, its radius and its font
through one rule that names the input types it covers. A type left off that
list is not unstyled on purpose. It is simply handed to the browser: a 1px
grey box with no padding, in the browser's own small font, sitting beside
the house's buttons. Until 2026-09-29 that was the search box above every
list (list_view(), on some forty pages), every number field (a hundred and
twenty of them: prices, nights, covers, percentages), every time field on
the rota, and the url, month and datetime-local boxes. Nothing errored and
every page rendered. The boxes just looked like a different app.

The same list is repeated in the (pointer: coarse) block, because iOS zooms
the whole page into any box whose text is under 16px and leaves it scrolled
sideways, and a bare `input` there loses to `input[type=text]` on
specificity. A type missing from the first list is usually missing from the
second as well.

ASKED OF THE CASCADE, NOT OF A SPELLING, with the model test_row_headings
built for a table cell. The types are read from the staff templates, not
listed here, so a new one cannot arrive without being asked about. For each,
a plain box in a staff form is resolved as a browser resolves it, at desk and
phone width, and must come out with the padding, border, radius and font a
text box gets, and 16px text on a phone. Then every real box on every staff
template is resolved again with the elements around it and its own style
attribute, and each must get its padding and its border from the stylesheet
or the page, never from the browser. A rule the model cannot read that could
reach a box fails the run rather than being left out.

The public pages are not in this. They are drawn with gudanes.css alone and
arrive from the design side as whole files.
"""
import os
import re
from html.parser import HTMLParser

from _harness import Suite
import _harness
import test_row_headings as rh

ROOT = _harness.ROOT
CSS_PATH = os.path.join(ROOT, "static", "style.css")
TEMPLATES = os.path.join(ROOT, "templates")
ENVS = (rh.DESK, rh.PHONE)

# Inputs that are not a box to type into: buttons, ticks, a slider, a
# swatch, and the ones nobody sees.
NOT_A_BOX = {"hidden", "submit", "button", "reset", "image", "checkbox", "radio",
             "range", "color"}
PADDING = tuple("padding-" + s for s in rh.SIDES)
BORDER = tuple("border-" + s for s in rh.SIDES)
HOUSE = PADDING + BORDER + ("border-radius", "font-family", "font-size")


# ---------------------------------------------------------------------------
# The cascade for one box.

def _declared(c, path, env, style=""):
    """{prop: value} for a box: the stylesheet, then its style attribute.
    A property nothing declares is absent, which is what 'the browser' means."""
    got = dict(c.declared(path, env))
    for prop, value, important in rh._declarations(style or ""):
        got[prop] = value
    return {p: " ".join(v.lower().split()) for p, v in got.items()}


def _px(value):
    m = re.fullmatch(r"([\d.]+)px", value or "")
    return float(m.group(1)) if m else None


def _box(type_, cls=""):
    """A box of one type in a form on a staff page, from the document down."""
    return [rh.El("html"), rh.El("body", "staff-shell"), rh.El("main", "page"),
            rh.El("div", "detail-card"), rh.El("form"),
            rh.El("input", cls, {"type": type_})]


def type_judgements(css, types):
    """({question: [offenders]}, cascade) for a plain box of every type."""
    out = {q: [] for q in ("house", "types", "phone", "read")}
    try:
        c = rh.Cascade(css)
    except ValueError as e:
        out["read"].append(str(e))
        return out, None
    house = {}
    for env in ENVS:
        text = _declared(c, _box("text"), env)
        house[env["name"]] = text
        missing = [p for p in PADDING + BORDER if p not in text]
        if missing or any(text[p] == "none" for p in BORDER):
            out["house"].append("a text box at %s width has %s from the browser"
                                % (env["name"], ", ".join(missing) or "no border"))
        for other in ("select", "textarea"):
            path = _box("text")[:-1] + [rh.El(other)]
            got = _declared(c, path, env)
            differ = [p for p in PADDING + BORDER if got.get(p) != text.get(p)]
            if differ:
                out["house"].append("a %s at %s width differs from a text box in %s"
                                    % (other, env["name"], ", ".join(differ)))
    for t in sorted(types):
        for env in ENVS:
            got = _declared(c, _box(t), env)
            ref = house[env["name"]]
            differ = ["%s %s (a text box has %s)" % (p, got.get(p, "from the browser"),
                                                     ref.get(p, "the browser's"))
                      for p in HOUSE if got.get(p) != ref.get(p)]
            if differ:
                out["types"].append("type=%s at %s width: %s" % (t, env["name"], "; ".join(differ[:3])))
        size = _px(_declared(c, _box(t), rh.PHONE).get("font-size"))
        if size is None or size < 16:
            out["phone"].append("type=%s is %s on a phone, so iOS zooms the page into it"
                                % (t, _declared(c, _box(t), rh.PHONE).get("font-size",
                                                                         "the browser's size")))
    out["read"] += c.unread
    return out, c


# ---------------------------------------------------------------------------
# The templates, as trees, so a box can be resolved with what is around it.

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}


def tame(src):
    """Every Jinja tag, expression and comment blanked at the same length, so
    line numbers hold and a > inside {{ }} does not end the tag around it."""
    return re.sub(r"\{#.*?#\}|\{\{.*?\}\}|\{%.*?%\}",
                  lambda m: re.sub(r"[^\n]", " ", m.group(0)), src, flags=re.S)


class _Node:
    def __init__(self, tag, attrs, parent, line):
        self.tag, self.attrs, self.parent, self.line = tag, attrs, parent, line
        self.children = []


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = _Node("#root", {}, None, 0)
        self.inputs = []

    def _add(self, tag, attrs):
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self.cur, self.getpos()[0])
        self.cur.children.append(node)
        if tag == "input":
            self.inputs.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._add(tag, attrs)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self._add(tag, attrs)

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:          # an end tag nothing opened is left alone
            self.cur = n.parent


def tree(src):
    t = _Tree()
    t.feed(tame(src))
    t.close()
    return t


def _el(node):
    sibs = node.parent.children if node.parent else [node]
    return rh.El(node.tag, node.attrs.get("class", ""),
                 {k: v for k, v in node.attrs.items() if k != "class"},
                 first=sibs[0] is node, last=sibs[-1] is node)


def _chain(node, stop):
    out = []
    while node is not None and node is not stop:
        out.append(_el(node))
        node = node.parent
    return out[::-1]


def shell_of(src):
    """The elements a page's content sits inside: those open where the base
    template says {% block content %}."""
    at = src.find("{% block content")
    t = _Tree()
    t.feed(tame(src[:at if at >= 0 else len(src)]))
    return _chain(t.cur, t.root)


def boxes_in(name, src, shells):
    """(label, type, path, style) for every box to type into in one template.
    An input with no type is a text box to the browser, and is kept as None
    so it can be named: `input[type=text]` does not reach it."""
    ext = re.search(r'\{%-?\s*extends\s+["\']([^"\']+)["\']', src)
    shell = [] if name in shells else shells.get(ext.group(1) if ext else "base.html",
                                                 shells["base.html"])
    out = []
    for node in tree(src).inputs:
        t = node.attrs.get("type")
        t = t.strip().lower() if t is not None else None
        if t in NOT_A_BOX:
            continue
        out.append(("%s:%d" % (name, node.line), t, shell + _chain(node, None)[1:],
                    node.attrs.get("style", "")))
    return out


def own_sheet(src):
    """A page's own <style> blocks, which are part of what draws its boxes."""
    code = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
    return "\n".join(tame(m) for m in re.findall(r"<style\b[^>]*>(.*?)</style>", code, re.S | re.I))


def box_judgements(css, boxes, sheets):
    """{question: [offenders]} for every real box, resolved where it sits."""
    out = {q: [] for q in ("untyped", "browser", "read")}
    cascades = {}
    for label, t, path, style in boxes:
        if t is None:
            out["untyped"].append(label)
            continue
        extra = sheets.get(label.split(":")[0], "")
        if extra not in cascades:
            try:
                cascades[extra] = rh.Cascade(css + "\n" + extra)
            except ValueError as e:
                out["read"].append(str(e))
                cascades[extra] = None
        c = cascades[extra]
        if c is None:
            continue
        for env in ENVS:
            got = _declared(c, path, env, style)
            missing = [p for p in PADDING + BORDER if p not in got]
            if missing:
                out["browser"].append("%s type=%s (%s) takes %s from the browser"
                                      % (label, t, env["name"],
                                         "its padding" if set(missing) <= set(PADDING)
                                         else "its border" if set(missing) <= set(BORDER)
                                         else "its padding and border"))
    for c in cascades.values():
        if c is not None:
            out["read"] += [u for u in c.unread if u not in out["read"]]
    return out


# ---------------------------------------------------------------------------
# Stylesheets to put the questions to, known right and known wrong.

GOOD = """
input[type=text], input[type=email], input[type=date],
input[type=number], input[type=search], input[type=time],
select, textarea{
  width:100%; font-family:'Inter', sans-serif; font-size:14px;
  padding:10px 12px; border:1px solid #ddd; border-radius:7px; }
.list-search input[type="search"]{ margin:0; flex:1; min-width:0; }
.owed-take__form input[type=number]{ width:7em; }
@media (pointer: coarse){
  input, select, textarea,
  input[type=text], input[type=email], input[type=date],
  input[type=number], input[type=search], input[type=time]{ font-size:16px; } }
"""
PROBE_TYPES = {"text", "email", "date", "number", "search", "time"}
PROBE_PAGE = """{% extends "base.html" %}{% block content %}
<div class="list-toolbar"><form method="get" class="list-search">
  <input type="hidden" name="tab" value="x">
  <input type="search" name="q"><button type="submit" class="btn-mini">Search</button></form></div>
<form method="post" class="owed-take__form">
  <input type="number" name="amount" step="0.01"><input type="text" name="note">
  <input type="checkbox" name="all"><button type="submit">Take it</button></form>
<form method="post"><input name="nothing-said"></form>
<form method="post"><input type="time" name="t" style="padding:0;"></form>
{% endblock %}"""


def run():
    s = Suite("Every box to type into is drawn by the house")
    css = open(CSS_PATH, encoding="utf-8").read()
    srcs, public = rh._public_names()
    shells = {n: shell_of(srcs[n]) for n in ("base.html", "pos_base.html")}

    s.section("The types a staff template uses, read from the templates")
    boxes, sheets = [], {}
    for name in sorted(srcs):
        if name in public:
            continue
        found = boxes_in(name, srcs[name], shells)
        boxes += found
        if found:
            sheets[name] = own_sheet(srcs[name])
    types = {t for _l, t, _p, _s in boxes if t}
    templates = len({b[0].split(":")[0] for b in boxes})
    s.check("the sweep reads %d boxes on %d staff templates, of %d types: %s"
            % (len(boxes), templates, len(types), ", ".join(sorted(types))),
            len(boxes) > 700 and templates > 150 and {"text", "number", "search", "date"} <= types,
            detail="it found too few to be reading the templates at all")
    s.check("the shell a staff page sits in is read from base.html",
            {("body", "staff-shell"), ("main", "page")}
            <= {(e.tag, " ".join(sorted(e.cls))) for e in shells["base.html"]}
            and any("till-shell" in e.cls for e in shells["pos_base.html"]),
            detail="a rule may name the shell (the till draws its own boxes), so a "
                   "box's path must carry it")

    s.section("A box of each type, resolved the way a browser resolves it")
    found, _c = type_judgements(css, types)
    s.check("a text box, a select and a textarea have the house padding and border",
            not found["house"], detail="; ".join(found["house"][:3]))
    s.check("every one of the %d types has the padding, border, radius and font a text "
            "box has, at desk and at phone width" % len(types),
            not found["types"],
            detail="%d, e.g. %s -- add the type to the rule in style.css that begins "
                   "input[type=text]" % (len(found["types"]), found["types"][:3]))
    s.check("and every one is 16px on a phone, so iOS does not zoom the page into it",
            not found["phone"],
            detail="%d, e.g. %s -- the (pointer: coarse) block spells out the same list"
                   % (len(found["phone"]), found["phone"][:3]))
    s.check("every rule that could reach a box of any type was read", not found["read"],
            detail="cannot evaluate: %s" % "; ".join(found["read"][:3]))

    s.section("Every box on a staff template, resolved where it sits")
    got = box_judgements(css, boxes, sheets)
    s.check("every box names its type, so the rule written for its type reaches it",
            not got["untyped"],
            detail="%d, e.g. %s -- an <input> with no type is a text box to the browser, "
                   "but input[type=text] does not reach it" % (len(got["untyped"]), got["untyped"][:4]))
    s.check("every one takes its padding and border from the stylesheet or the page, "
            "not the browser",
            not got["browser"],
            detail="%d, e.g. %s" % (len(got["browser"]), got["browser"][:3]))
    s.check("every rule that could reach one was read", not got["read"],
            detail="cannot evaluate: %s" % "; ".join(got["read"][:3]))

    s.section("And the sweep can tell the difference")
    probe = boxes_in("probe.html", PROBE_PAGE, shells)
    s.check("a search box, a number, a text box, a box with no type and a time box are "
            "found in the page's shell; a hidden input and a tick are not",
            [(p[0], p[1]) for p in probe] == [("probe.html:4", "search"), ("probe.html:6", "number"),
                                              ("probe.html:6", "text"), ("probe.html:8", None),
                                              ("probe.html:9", "time")]
            and all(p[2][0].tag == "html" and "staff-shell" in p[2][1].cls for p in probe)
            and "list-search" in probe[0][2][-2].cls,
            detail="found %s" % [(p[0], p[1]) for p in probe])
    s.check("a page's own <style> is read, and one inside a comment is not",
            own_sheet("{# <style>.x{a:b}</style> #}<style>.y input{padding:0}</style>")
            == ".y input{padding:0}", detail="the cascade for a page is style.css, then these")

    s.section("And the questions can come out wrong")
    # Each question above, put to a stylesheet that is right and then to ones
    # that are wrong in one way each. A question that passes a broken one is
    # not asking anything.
    def verdicts(variant):
        t, _c = type_judgements(variant, PROBE_TYPES)
        b = box_judgements(variant, [p for p in probe if p[3] == "" and p[1]], {})
        return {k for k, v in list(t.items()) + list(b.items()) if v}

    s.check("a stylesheet that does it right passes every question",
            verdicts(GOOD) == set(), detail="failed on it: %s" % sorted(verdicts(GOOD)))
    broken = [
        ("the list as it was, with no number, search or time, is seen",
         GOOD.replace("input[type=number], input[type=search], input[type=time],\nselect",
                      "select"),
         {"types", "browser"}),
        ("a type in the first list and missing from the phone list is seen",
         GOOD.replace("input[type=number], input[type=search], input[type=time]{",
                      "input[type=search], input[type=time]{"),
         {"types", "phone"}),
        ("a later rule that takes the border off one type is seen",
         GOOD + "input[type=search]{ border:none; }", {"types"}),
        ("a rule that gives one type only padding leaves its border to the browser",
         GOOD.replace("input[type=number], ", "") + "input[type=number]{ padding:10px 12px; }",
         {"types", "browser"}),
        ("a text box that loses the house look is seen, whatever the others do",
         GOOD.replace("input[type=text], ", ""), {"house", "types", "browser"}),
        ("a rule the model cannot read is reported, not skipped",
         GOOD + "form:has(> button) input{ padding:0; }", {"read"}),
    ]
    for label, variant, questions in broken:
        bad = verdicts(variant)
        s.check(label, bad == questions,
                detail="expected exactly %s to fail; failed: %s" % (sorted(questions), sorted(bad)))
    b = box_judgements(GOOD, probe, {})
    s.check("a box with no type is named, and a style attribute that sets padding is its "
            "own business",
            b["untyped"] == ["probe.html:8"] and not b["browser"],
            detail="got %s" % b)
    return s


if __name__ == "__main__":
    print(run().report())
