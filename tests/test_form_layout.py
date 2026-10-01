"""A form lays itself out the way it is written: in a row, or in a stack.

style.css gave every form `display:flex; flex-direction:column; gap:4px`, to
stack its fields. Measured in Chrome on 2026-09-29, it did two things nobody
asked for, and neither errored:

- A form on its own in a table cell is a single button, and a column
  flexbox stretches what it holds across it. A .data-table is 100% wide, so
  the spare width goes to the columns: "Off the list" ran about 190px wide on
  an event's guest list, and "Fold into this one", "Done", "Charge" and
  "Send it" the same, on some thirty pages.

- A form written as a row -- display:flex, a gap, align-items:center -- kept
  the stack's column, because nothing of its own said which way. Thirty-seven
  were written like that on thirty staff pages, ten rules in style.css made
  one by class (the search and sort above every list among them), and two
  pages did it in a <style> of their own. Every one stacked, centred or
  pushed right wherever it asked to align its items: Charge and Let it go one
  above the other in the middle of the cell. The forms that read as rows were
  the ones whose authors had noticed and written the direction out, and
  gudanes.css carries a whole section headed DEFENCE AGAINST style.css for
  the same reason.

So the stack is now a one-column grid, which stacks without having a
direction to hand on, and a form directly in a data-table cell is a block.

ASKED OF THE CASCADE, NOT OF A SPELLING, with the model of the stylesheet
test_row_headings built for a table cell. Every form on every staff template
is found with the elements around it, and what reaches it -- style.css, then
gudanes.css where the page links it, then the page's own <style>, then its
style attribute -- is resolved for it as a browser resolves it, at desk and
at phone width. A form hidden until a script shows it is judged as shown.
What comes out is asked four things:

1. A form written as a flexbox is one, and runs the way it says, or as a row
   when it says nothing. Its direction may not come from the rule written
   for every form: that is the leak.
2. A form whose own rules give it a flex direction or wrap, and no display,
   is a flexbox all the same. It is leaning on a display nobody wrote for it,
   which is what .new-section-form and the photo intake form did, and a grid
   would quietly stack them.
3. A form with no layout of its own is a stack of fields across it, a single
   column whose items stretch. This is the half that stops the fix above
   being made by taking the stack away from the three hundred that are one.
4. A form directly in a data-table cell does not stretch its button, and one
   that holds nothing but a button sits where the cell's text-align puts it.

Each is asked again of stylesheets that are wrong in one way, and must come
out wrong there and only there. A rule the model cannot read that could reach
a form fails the run rather than being left out.

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
STATIC = os.path.join(ROOT, "static")
TEMPLATES = os.path.join(ROOT, "templates")
ENVS = (rh.DESK, rh.PHONE)

# The properties that decide how a form lays out what it holds.
PROPS = ("display", "flex-direction", "flex-wrap", "align-items", "justify-items",
         "grid-template-columns", "grid-auto-flow")
FLEX_ONLY = ("flex-direction", "flex-wrap")
UA = {"display": "block"}               # what Chrome's own stylesheet gives a form
INITIAL = {"display": "inline", "flex-direction": "row", "flex-wrap": "nowrap",
           "align-items": "normal", "justify-items": "legacy",
           "grid-template-columns": "none", "grid-auto-flow": "row"}
FLEX = ("flex", "inline-flex")
GRID = ("grid", "inline-grid")
# Where a lone button keeps its own width and follows the cell's text-align.
FLOWS = ("block", "flow-root", "inline", "inline-block", "list-item", "contents")
STRETCH = ("normal", "stretch", "legacy")
# At-rules whose rules still apply under a condition. Kept, under a condition
# the model cannot evaluate, so that one reaching a form is reported.
GROUPS = ("@supports", "@container", "@layer", "@scope", "@document")


# ---------------------------------------------------------------------------
# The stylesheets.

def parse(css):
    """rh.parse, except that a rule inside @supports, @container or @layer is
    kept under its condition rather than passed over. gudanes.css has twelve
    such blocks; left out, a rule in one that laid out a form would simply
    not exist here."""
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
            elif prelude.split(None, 1)[0] in GROUPS if prelude.startswith("@") else False:
                walk(i + 1, j, prelude if media is None else "(nested)")
            elif not prelude.startswith("@"):
                out.append((media, prelude, css[i + 1:j], len(out)))
            k = j + 1

    walk(0, len(css), None)
    return out


def _expand(decls):
    """rh._declarations, with the shorthands that set these properties."""
    out = []
    for prop, value, important in decls:
        if prop == "flex-flow":
            for word in value.split():
                if word in ("row", "row-reverse", "column", "column-reverse"):
                    out.append(("flex-direction", word, important))
                elif word in ("wrap", "nowrap", "wrap-reverse"):
                    out.append(("flex-wrap", word, important))
        elif prop == "place-items":
            v = value.split()
            out += [("align-items", v[0], important), ("justify-items", v[-1], important)]
        elif prop in ("grid", "grid-template"):
            out.append(("grid-template-columns", None, important))   # not modelled: unread
        else:
            out.append((prop, value, important))
    return [d for d in out if d[0] in PROPS]


def rules_of(css, first_order=0):
    """The rules in one stylesheet that set anything a form's layout reads."""
    out, unread = [], []
    for media, group, body, order in parse(css):
        decls = _expand(rh._declarations(body))
        if not decls:
            continue
        for sel in rh._split(group):
            try:
                steps = rh._steps(sel)
                subject = rh._parts(steps[-1][1])
                spec = rh.specificity(sel)
            except rh._Unread as e:
                unread.append("%s (%s)" % (sel, e))
                continue
            every_form = len(steps) == 1 and [p.lower() for p in subject] == ["form"]
            out.append((media, sel, subject, spec, decls, first_order + order, every_form))
    return out, unread


# ---------------------------------------------------------------------------
# Selectors that hold another selector: the cell model reads :not() of a
# compound, and a form's own rule is written :where(.data-table td) > form.

def _compound_at(compound, path, i):
    el = path[i]
    for part in sorted(rh._parts(compound), key=lambda p: p.startswith(":")):
        if part.startswith(":") and not part.startswith("::"):
            name, _, arg = part[1:].partition("(")
            if name in ("where", "is"):
                if not any(matches_at(x, path, i) for x in rh._split(arg[:-1])):
                    return False
                continue
            if name == "not":
                if any(matches_at(x, path, i) for x in rh._split(arg[:-1])):
                    return False
                continue
        if not rh._simple(part, el):
            return False
    return True


def matches_at(selector, path, i):
    """Does `selector` reach path[i], given the elements above it?"""
    steps = rh._steps(selector)

    def at(si, pi):
        comb, comp = steps[si]
        if not _compound_at(comp, path, pi):
            return False
        if si == 0:
            return True
        if comb == ">":
            return pi > 0 and at(si - 1, pi - 1)
        if comb == " ":
            return any(at(si - 1, pj) for pj in range(pi - 1, -1, -1))
        raise rh._Unread("the %r combinator" % comb)

    return at(len(steps) - 1, i)


def _could_reach(subject, el):
    """False only when the subject plainly names something else."""
    for part in subject:
        if part[0].isalpha() and part.lower() not in (el.tag, "*"):
            return False
        if part[0] == "." and part[1:] not in el.cls:
            return False
        if part[0] == "#" and el.attrs.get("id") != part[1:]:
            return False
    return True


class Won:
    """One property of one form: its value, and the declaration that won it."""

    def __init__(self, value, rank=None, source="initial", every_form=False):
        self.value, self.rank, self.source, self.every_form = value, rank, source, every_form


class FormCascade:
    """Everything that reaches a form on one page, in document order."""

    def __init__(self, sheets):
        self.rules, self.unread = [], []
        order = 0
        for css in sheets:
            got, unread = rules_of(css, order)
            self.rules += got
            self.unread += [u for u in unread if u not in self.unread]
            order += 10 ** 6

    def _note(self, what):
        if what not in self.unread:
            self.unread.append(what)

    def resolve(self, path, style, env):
        """({prop: Won}, {prop: Won written for this form}) at one width.

        The second is what the form's own rules and style attribute say, with
        the rules written for every form left out: what its author asked for.
        """
        el, won, own = path[-1], {}, {}
        for media, sel, subject, spec, decls, order, every_form in self.rules:
            if not _could_reach(subject, el):
                continue
            applies = True if media is None else rh._media(media, env)
            if applies is False:
                continue
            try:
                hit = matches_at(sel, path, len(path) - 1)
            except rh._Unread as e:
                self._note("%s (%s)" % (sel, e))
                continue
            if not hit:
                continue
            if applies is None:
                self._note("@media %s { %s }" % (media, sel))
                continue
            for prop, value, important in decls:
                if value is None:
                    self._note("%s { a %s shorthand }" % (sel, prop))
                    continue
                w = Won(" ".join(value.lower().split()), (important, False, spec, order), sel, every_form)
                for into in (won,) if every_form else (won, own):
                    if prop not in into or w.rank >= into[prop].rank:
                        into[prop] = w
        for prop, value, important in _expand(rh._declarations(style or "")):
            if value is None:
                self._note("style=%r (a %s shorthand)" % (style, prop))
                continue
            w = Won(" ".join(value.lower().split()), (important, True, (0, 0, 0), 0),
                    "its style attribute")
            for into in (won, own):
                if prop not in into or w.rank >= into[prop].rank:
                    into[prop] = w
        for prop in PROPS:
            if prop not in won:
                won[prop] = Won(UA[prop], None, "the browser") if prop in UA else Won(INITIAL[prop])
        return won, own


def shown(style):
    """A form's style attribute as it is once a script has shown the form.
    Hidden with display:none, it is shown by taking that away or by setting
    display:flex, and either way what lays it out then is its own rules."""
    if not style or not re.search(r"display\s*:\s*none", style):
        return style
    return ";".join(d for d in style.split(";") if not re.match(r"\s*display\s*:", d))


# ---------------------------------------------------------------------------
# What the resolved values mean.

def is_stack(r):
    """A single column whose items stretch across it."""
    d = r["display"].value
    if d in GRID:
        cols = r["grid-template-columns"].value
        one = cols == "none" or (len(cols.split()) == 1 and not cols.startswith("repeat("))
        return one and r["grid-auto-flow"].value.startswith("row") and r["justify-items"].value in STRETCH
    if d in FLEX:
        return r["flex-direction"].value.startswith("column") and r["align-items"].value in STRETCH
    return False


def _from(w):
    return w.source if w.source in ("initial", "the browser", "its style attribute") else "`%s`" % w.source


QUESTIONS = ("row", "flex", "stack", "cell", "lone", "read")


def judgements(sheets_for, forms, env_list=ENVS):
    """({question: [offenders]}, {question: count}) for a set of forms.

    `sheets_for(form)` gives the stylesheets that reach that form's page, so
    the same questions can be put to a stylesheet known to be wrong.
    """
    out = {q: [] for q in QUESTIONS}
    counts = {q: 0 for q in QUESTIONS}
    cascades = {}
    for form in forms:
        name, path, style, in_cell, lone = form
        sheets = sheets_for(form)
        if sheets not in cascades:
            try:
                cascades[sheets] = FormCascade(sheets)
            except ValueError as e:
                out["read"].append(str(e))
                continue
        c = cascades[sheets]
        for env in env_list:
            r, own = c.resolve(path, shown(style), env)
            where = "%s (%s)" % (name, env["name"])
            d = r["display"]
            mine = own.get("display")
            if mine is not None and mine.value in FLEX:
                counts["row"] += 1
                if d.value not in FLEX:
                    out["row"].append("%s is written as a flexbox and is display:%s by %s"
                                      % (where, d.value, _from(d)))
                elif r["flex-direction"].every_form:
                    out["row"].append("%s runs as a %s: made a flexbox by %s, turned by %s"
                                      % (where, r["flex-direction"].value, _from(d),
                                         _from(r["flex-direction"])))
            elif mine is None and any(p in own for p in FLEX_ONLY):
                counts["flex"] += 1
                if d.value not in FLEX:
                    p = [p for p in FLEX_ONLY if p in own][0]
                    out["flex"].append("%s has its %s from %s and no display of its own, so "
                                       "display:%s by %s leaves it doing nothing"
                                       % (where, p, _from(own[p]), d.value, _from(d)))
            elif mine is None:
                counts["stack"] += 1
                if not is_stack(r):
                    out["stack"].append("%s is display:%s by %s, not a column of fields across it"
                                        % (where, d.value, _from(d)))
            if in_cell:
                counts["cell"] += 1
                if is_stack(r):
                    out["cell"].append("%s stretches what it holds across the column: display:%s by %s"
                                       % (where, d.value, _from(d)))
                if lone:
                    counts["lone"] += 1
                    if d.value not in FLOWS:
                        out["lone"].append("%s is display:%s by %s, so its button ignores the "
                                           "cell's text-align" % (where, d.value, _from(d)))
    for c in cascades.values():
        out["read"] += [u for u in c.unread if u not in out["read"]]
    return out, counts


# ---------------------------------------------------------------------------
# The templates, as trees.

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}
BUTTON_INPUTS = {"submit", "button", "image", "reset"}


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
        self.forms = []

    def _add(self, tag, attrs):
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self.cur, self.getpos()[0])
        self.cur.children.append(node)
        if tag == "form":
            self.forms.append(node)
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


def _controls(node, into):
    for c in node.children:
        if c.tag in ("noscript", "template"):
            continue                    # a browser running scripts shows neither
        if c.tag in ("button", "select", "textarea") or (
                c.tag == "input" and c.attrs.get("type", "text") != "hidden"):
            into.append(c)
        _controls(c, into)
    return into


def in_data_cell(node):
    if not node.parent or node.parent.tag not in ("td", "th"):
        return False
    n = node.parent
    while n is not None and n.tag != "table":
        n = n.parent
    return n is not None and "data-table" in n.attrs.get("class", "").split()


def lone_button(node):
    ctl = _controls(node, [])
    return len(ctl) == 1 and (ctl[0].tag == "button" or ctl[0].attrs.get("type") in BUTTON_INPUTS)


def forms_in(name, src, shells):
    """(label, path, style, in_cell, lone) for every form in one template."""
    ext = re.search(r'\{%-?\s*extends\s+["\']([^"\']+)["\']', src)
    if name in shells:
        shell = []                      # the shell's own forms sit in its own tree
    else:
        shell = shells.get(ext.group(1) if ext else "base.html", shells["base.html"])
    t = tree(src)
    return [("%s:%d" % (name, f.line), shell + _chain(f, t.root), f.attrs.get("style", ""),
             in_data_cell(f), lone_button(f)) for f in t.forms]


_LINKS_GUDANES = re.compile(r"<link\b[^>]*gudanes\.css", re.I)


def own_sheets(src):
    """What a template adds to the cascade: gudanes.css if it links it, then
    its own <style> blocks, in the order a browser meets them."""
    code = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
    out = []
    for m in re.finditer(r"<link\b[^>]*gudanes\.css[^>]*>|<style\b[^>]*>(.*?)</style>", code, re.S | re.I):
        out.append("gudanes.css" if m.group(1) is None else tame(m.group(1)))
    return out


# ---------------------------------------------------------------------------
# Forms to put the questions to when the stylesheet is known to be wrong.

PROBE_PAGE = """
<div class="detail-card">
  <form method="post"><label class="field-label">Name</label><input type="text" name="n">
    <button type="submit" class="btn-primary">Save</button></form>
  <form method="post" style="display:flex; gap:6px; align-items:center;">
    <input type="text" name="amount" style="width:90px;">
    <button type="submit" class="small-link">Charge</button>
    <button type="submit" class="small-link">Let it go</button></form>
  <form method="post" class="register-form"><div class="register-field"><input type="text"></div>
    <button type="submit" class="btn-mini">Register them</button></form>
  <form method="post" class="search-bar" style="display:none; margin-top:6px;">
    <div><input type="text" name="q"></div><button type="submit" class="btn-mini">Save</button></form>
</div>
<div class="table-wrap"><table class="data-table"><tbody><tr>
  <td><form method="post"><input type="hidden" name="csrf_token" value="x">
    <button type="submit" class="btn-mini">Off the list</button></form></td>
  <td style="text-align:right;"><form method="post"><button type="submit" class="btn-mini">Run now</button></form></td>
  <td><form method="post" style="display:flex; gap:6px;">
    <button type="submit" class="small-link">Approve</button>
    <button type="submit" class="small-link">Decline</button></form></td>
</tr></tbody></table></div>
"""

GOOD = """
form{ display:grid; grid-template-columns:100%; grid-auto-rows:max-content; gap:4px; }
:where(.data-table td) > form{ display:block; }
.register-form{ display:flex; flex-wrap:wrap; gap:10px; align-items:flex-end; }
.search-bar{ display:flex; flex-direction:row; gap:14px; flex-wrap:wrap; }
"""
OLD = "form{ display:flex; flex-direction:column; gap:4px; }"
NEW = "form{ display:grid; grid-template-columns:100%; grid-auto-rows:max-content; gap:4px; }"
CELL = ":where(.data-table td) > form{ display:block; }"


def run():
    s = Suite("A form lays itself out the way it is written")
    style_css = open(os.path.join(STATIC, "style.css"), encoding="utf-8").read()
    gudanes_css = open(os.path.join(STATIC, "gudanes.css"), encoding="utf-8").read()
    shells = {n: shell_of(open(os.path.join(TEMPLATES, n), encoding="utf-8").read())
              for n in ("base.html", "pos_base.html")}

    s.section("The page a staff template's content sits in")
    staff_shell = [(e.tag, " ".join(sorted(e.cls))) for e in shells["base.html"]]
    s.check("read from base.html, down to where the content goes",
            ("body", "staff-shell") in staff_shell and ("main", "page") in staff_shell,
            detail="found %s -- a rule may name the shell, so a form's path must carry it"
                   % staff_shell)

    s.section("Every form on a staff template, resolved the way a browser resolves it")
    srcs, public = rh._public_names()
    forms, extra = [], {}
    for name in sorted(srcs):
        if name in public:
            continue
        mine = tuple(gudanes_css if x == "gudanes.css" else x for x in own_sheets(srcs[name]))
        for f in forms_in(name, srcs[name], shells):
            forms.append(f)
            extra[f[0]] = mine
    templates = len({f[0].split(":")[0] for f in forms})
    s.check("the sweep reads %d forms on %d staff templates" % (len(forms), templates),
            len(forms) > 500 and templates > 150,
            detail="it found too few to be reading the templates at all")
    with_own = sorted({f[0].split(":")[0] for f in forms if extra[f[0]]})
    s.check("and takes in the stylesheets %d of them add: %s" % (len(with_own), ", ".join(with_own)),
            {"admin_photo_intake.html", "door_lock.html", "management_social_connect.html",
             "pass.html"} <= set(with_own),
            detail="a page's own <style>, and gudanes.css where a staff page links it, are "
                   "part of what lays its forms out")
    found, counts = judgements(lambda f: (style_css,) + extra[f[0]], forms)
    s.check("%d written as a flexbox, counting desk and phone, and every one runs the way it "
            "says, or as a row when it says nothing" % counts["row"],
            counts["row"] > 250 and not found["row"],
            detail="%d, e.g. %s" % (len(found["row"]), found["row"][:3]))
    s.check("every one whose own rules give it a flex direction or wrap is a flexbox (%d)"
            % counts["flex"],
            not found["flex"], detail="%d, e.g. %s" % (len(found["flex"]), found["flex"][:3]))
    s.check("%d with no layout of their own, and every one is a column of fields across it"
            % counts["stack"],
            counts["stack"] > 500 and not found["stack"],
            detail="%d, e.g. %s" % (len(found["stack"]), found["stack"][:3]))
    s.check("%d directly in a data-table cell, and none stretches what it holds across the column"
            % counts["cell"],
            counts["cell"] > 100 and not found["cell"],
            detail="%d, e.g. %s" % (len(found["cell"]), found["cell"][:3]))
    s.check("%d of those are a button and nothing else, and each sits where its cell's "
            "text-align puts it" % counts["lone"],
            counts["lone"] > 75 and not found["lone"],
            detail="%d, e.g. %s" % (len(found["lone"]), found["lone"][:3]))
    s.check("every rule that could reach a form was read", not found["read"],
            detail="cannot evaluate: %s" % "; ".join(found["read"][:3]))

    s.section("And the sweep can tell the difference")
    probe = forms_in("probe.html", '{% extends "base.html" %}{% block content %}'
                     + PROBE_PAGE + "{% endblock %}", shells)
    s.check("a stack, a row by style, a row by class, a hidden form and three in cells are all "
            "found, inside the page's shell",
            len(probe) == 7 and all(p[1][0].tag == "html" for p in probe)
            and [p[3] for p in probe] == [False] * 4 + [True] * 3
            and [p[4] for p in probe] == [False] * 4 + [True, True, False],
            detail="found %s" % [(p[0], p[3], p[4]) for p in probe])
    s.check("a hidden input is not a control, and a button in <noscript> does not count",
            lone_button(tree('<form><input type="hidden" name="a"><button>Go</button></form>').forms[0])
            and not lone_button(tree('<form><select name="s"></select><noscript><button>Apply'
                                     '</button></noscript></form>').forms[0])
            and lone_button(tree('<form><input type="submit" value="Go"></form>').forms[0]),
            detail="one button beside a hidden input is a lone button; a select with a "
                   "fallback button is not; an input of type submit is a button")
    s.check("a form in a table that is not a data table is not this rule's business",
            not forms_in("x.html", '<table><tr><td><form><button>Go</button></form></td></tr>'
                         '</table>', shells)[0][3],
            detail="only a .data-table is 100% wide")
    s.check("a page's own <style> and a linked gudanes.css are found, and a comment "
            "naming the sheet is not a link",
            own_sheets('{# gudanes.css is linked below #}<link rel="stylesheet" href="{{ '
                       "url_for('static', filename='gudanes.css') }}\"><style>.x{display:flex}"
                       '</style>') == ["gudanes.css", ".x{display:flex}"],
            detail="the cascade for a page is style.css, then these, in the order met")
    s.check("a hidden form is judged as it is once shown",
            shown("display:none; margin-top:6px;") == " margin-top:6px;"
            and shown("margin:0;") == "margin:0;",
            detail="its display:none is what a script takes away")

    s.section("And the questions can come out wrong")
    # Each question above, put to a stylesheet that is right and then to ones
    # that are wrong in one way each. A question that passes a broken one is
    # not asking anything, and the cascade is the part most likely to be
    # wrong, so it is the part proved here.
    got, _n = judgements(lambda f: (GOOD,), probe)
    s.check("a stylesheet that does it right passes every question",
            not any(got.values()), detail="failed on it: %s" % {k: v for k, v in got.items() if v})

    broken = [
        ("the old rule: a stack with a direction to hand on, and nothing for a cell",
         GOOD.replace(NEW, OLD).replace(CELL, ""), ["row", "cell", "lone"]),
        ("the old rule beside the cell rule still turns a row, even in a cell",
         GOOD.replace(NEW, OLD), ["row", "cell"]),
        ("a stack with no rule for a cell stretches the button across it",
         GOOD.replace(CELL, ""), ["cell", "lone"]),
        ("a later, weightier rule that stacks a cell's form again is seen",
         GOOD + ".data-table td > form{ display:flex; flex-direction:column; }", ["cell", "lone"]),
        ("a row written by class that leaves its display to the rule for every form is seen",
         GOOD.replace(".register-form{ display:flex; ", ".register-form{ "), ["flex"]),
        ("a stack whose fields no longer stretch is seen",
         GOOD.replace("gap:4px; }", "gap:4px; justify-items:start; }", 1), ["stack"]),
        ("taking the stack away altogether is seen",
         GOOD.replace(NEW, ""), ["stack"]),
        ("a stack forced on every form overrides a row, and that is seen",
         GOOD.replace("display:grid;", "display:grid !important;", 1), ["row", "cell", "lone"]),
        ("a rule the model cannot read is reported, not skipped",
         GOOD + "form:has(> button){ display:flex; }", ["read"]),
        ("and so is a rule inside @supports that could reach a form",
         GOOD + "@supports (display:grid){ .register-form{ display:grid; } }", ["read"]),
    ]
    for label, variant, questions in broken:
        got, _n = judgements(lambda f, v=variant: (v,), probe)
        bad = {k for k, v in got.items() if v}
        s.check(label, bad == set(questions),
                detail="expected exactly %s to fail; failed: %s" % (sorted(questions), sorted(bad)))
    return s


if __name__ == "__main__":
    print(run().report())
