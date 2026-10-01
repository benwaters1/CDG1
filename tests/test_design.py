"""Static checks on the stylesheet, for the two design faults that have
actually shipped here more than once.

The first is a variant class that a shell rule silently beats. `.btn-mini-danger`
is one class (0,1,0) and `body.staff-shell .btn-mini` is a class plus an element
plus a class (0,2,1), so the base rule wins and the variant renders identically
to a normal button. That hid the delete buttons, then the selected state of
every filter row. Both were invisible rather than broken, which is why clicking
around never found them.

The second is text that fails WCAG AA. Contrast is arithmetic, so it does not
need a browser, and two of the failures here came from an `opacity` on a rule
that was otherwise fine.

Neither check replaces looking at the page — they catch the class of fault that
looking at the page has repeatedly missed.
"""
import os
import re

from _harness import Suite, ROOT

CSS_PATH = os.path.join(ROOT, "static", "style.css")
# The public front end ships its own stylesheet layered on top of style.css, so
# a class can legitimately be defined in either. Checking only the first one
# reported every g- class in the new templates as unstyled.
import glob as _glob
ALL_CSS = sorted(_glob.glob(os.path.join(ROOT, "static", "*.css")))
AA_NORMAL = 4.5


def _strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _rules(css):
    return re.findall(r"([^{}]+)\{([^{}]*)\}", css)


def specificity(selector):
    ids = len(re.findall(r"#[\w-]+", selector))
    classes = len(re.findall(r"\.[\w-]+|\[[^\]]+\]|:[a-z-]+\(", selector))
    elements = len(re.findall(r"(?:^|[\s>+~])([a-z][\w-]*)", selector))
    return (ids, classes, elements)


def _declarations(body):
    """{property: has_important} for one rule body."""
    out = {}
    for prop, value in re.findall(r"([a-z-]+)\s*:\s*([^;]*)", body):
        out[prop.strip()] = "!important" in value
    return out


def _relative_luminance(rgb):
    channels = []
    for v in rgb:
        v /= 255.0
        channels.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(fg, bg):
    a, b = sorted((_relative_luminance(fg), _relative_luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def _hex(value):
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        return None
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _tokens(css):
    """Resolve the :root and staff-shell custom properties to rgb tuples."""
    out = {}
    for selector, body in _rules(css):
        if ":root" not in selector and "staff-shell" not in selector:
            continue
        scope = "staff" if "staff-shell" in selector else "root"
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", body):
            rgb = _hex(value)
            if rgb:
                out.setdefault(scope, {})[name.strip()] = rgb
    return out


def _co_occurring_classes():
    """Every pair of classes the templates put on the same element.

    Without this a shared name prefix looks like a variant relationship, and
    .sidebar-toggle gets reported as a broken variant of .sidebar when the two
    never appear on the same tag.
    """
    import glob
    pairs = set()
    for path in glob.glob(os.path.join(ROOT, "templates", "*.html")):
        html = open(path, encoding="utf-8").read()
        for attr in re.findall(r'class="([^"]*)"', html):
            names = [c for c in re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", attr).split()
                     if re.fullmatch(r"[a-zA-Z][\w-]*", c)]
            # Conditional variants live inside the Jinja, not beside it —
            # class="btn-mini {{ 'btn-mini-active' if ... }}". Stripping the
            # expression would drop precisely the classes this check is for.
            for literal in re.findall(r"['\"]([a-zA-Z][\w\- ]*)['\"]", attr):
                names += [c for c in literal.split() if re.fullmatch(r"[a-zA-Z][\w-]*", c)]
            for a in names:
                for b in names:
                    if a != b:
                        pairs.add((a, b))
    return pairs


def _aria_hidden_classes():
    """Classes that only ever appear on aria-hidden elements — decorative."""
    import glob
    hidden, seen = set(), set()
    for path in glob.glob(os.path.join(ROOT, "templates", "*.html")):
        html = open(path, encoding="utf-8").read()
        for tag in re.findall(r"<[a-z]+\s[^>]*>", html):
            match = re.search(r'class="([^"]*)"', tag)
            if not match:
                continue
            names = {c for c in re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", match.group(1)).split()
                     if re.fullmatch(r"[a-zA-Z][\w-]*", c)}
            seen |= names
            if 'aria-hidden="true"' in tag:
                hidden |= names
            else:
                hidden -= names
    return hidden


# ---------------------------------------------------------------------------
# A class on a staff page, against the stylesheets that page actually loads.
#
# base.html links style.css and nothing else, and the public pages link
# gudanes.css and nothing else. A class written into a staff template that
# only gudanes.css draws, or that nothing draws, comes out as plain text and
# nothing says so: .g-hint on fifteen staff pages, .muted on nine, .empty-note
# on seven, and the kitchen's wall mode, whose stamp printed as two loose words
# above the heading because every rule it had was in the other stylesheet.
#
# ASKED OF THE RULE, NOT OF THE SPELLING. A class counts as drawn on a page
# only if a rule in a stylesheet that page loads names it AND every other
# class that rule needs is on the page as well. .g-btn is spelled in
# style.css, under .g-nextf__i, which no staff page has -- so the Save button
# on the Instagram settings page, written <button class="g-btn">, was a bare
# browser button, and a check that looked for the name would have passed it.
#
# What a script selects on is a handle, not a look, and needs no rule. What a
# script ADDS needs one, asked more loosely: which element it lands on cannot
# be read from here, so any rule on the page that names it will do.

# Names an element carries for the reader, or as the hook its children's
# rules hang from, that no rule is meant to draw. Each says why. Checked both
# ways, like the known lists in run.py: a name some stylesheet starts drawing,
# or that no staff page uses any more, has to come off.
NAMED_NOT_DRAWN = {
    "profile-main": "the wide column of .profile-layout, placed by its grid",
    "profile-side": "the narrow column of .profile-layout",
    "manual-view": "the reading half of a manual section; its script finds it by id",
    "tpl-toggle": "already .btn-mini.btn-ghost; its script finds it by data-tpl-toggle",
    "tray-item__when": "the first line of a photograph's caption, in the caption's type",
    "od-today": "which panel of the office display this is; .od-panel draws it",
    "od-calendar": "which panel of the office display this is; .od-panel draws it",
    "pass__now": "one slot in the pass's bar; its label and its figure are drawn",
    "pi-drop__meta": "the wrapper round the chosen photograph's name and facts",
    "pi-edit__side": "the second column of .pi-edit, placed by its grid",
}

_IDENT = re.compile(r"-?[a-zA-Z_][\w-]*$")


def _template_parent(src):
    m = re.search(r'\{%-?\s*extends\s+["\']([^"\']+)["\']', src)
    return m.group(1) if m else None


def _is_page(src):
    """A template rendered as a page: it extends a shell, or it is a whole
    document. The <html tag is looked for in the markup only -- a script
    comment that mentioned the root element once made the kitchen wall's
    partial a page of its own, read without the stylesheet its pages load."""
    markup = re.sub(r"<script\b[^>]*>.*?</script>", "", src, flags=re.S | re.I)
    return bool(_template_parent(src) or re.search(r"<html\b", markup, re.I))


def _template_uses(src):
    """Every template this one includes, imports or takes a macro from."""
    return set(re.findall(r'\{%-?\s*(?:include|import|from)\s+["\']([^"\']+)["\']', src))


def _linked_stylesheets(src):
    return {m.group(1) for tag in re.findall(r"<link\b[^>]*>", src)
            if re.search(r"rel=[\"']stylesheet", tag)
            for m in [re.search(r"filename=['\"]([^'\"]+\.css)['\"]", tag)] if m}


def _style_blocks(src):
    return "\n".join(re.findall(r"<style\b[^>]*>(.*?)</style>", src, re.S))


def _inline_scripts(src):
    blocks = re.findall(r"<script\b(?![^>]*\bsrc=)(?![^>]*application/(?:ld\+)?json)"
                        r"[^>]*>(.*?)</script>", src, re.S)
    return "\n".join(blocks + re.findall(r'\son[a-z]+="([^"]*)"', src))


def _top_level_else(text):
    """Where the ' else ' of a conditional is, outside strings and brackets."""
    depth, quote = 0, None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and re.match(r"\selse\s", text[i:]):
            return i
    return None


def _jinja_branches(expr):
    """What a Jinja expression can print: a string for each literal it can
    come out as, None for a value that cannot be read from the template.

        'is-on' if x == 'y' else 'off'        ['is-on', 'off']
        'active' if request.endpoint == 'x'   ['active', '']
        row.state                             [None]

    The operands of the condition are never among them. 'admin_hr' in
    request.endpoint == 'admin_hr' is a page's name, not a class."""
    body = expr.strip()
    while body.startswith("(") and body.endswith(")"):
        body = body[1:-1].strip()
    m = re.fullmatch(r"(['\"])([^'\"]*)\1", body)
    if m:
        return [m.group(2)]
    m = re.match(r"(['\"])([^'\"]*)\1\s+if\s+(.*)$", body, re.S)
    if not m:
        return [None]
    cut = _top_level_else(m.group(3))
    if cut is None:
        return [m.group(2), ""]
    return [m.group(2)] + _jinja_branches(m.group(3)[cut + 5:])


def _feed(words, text):
    """Append text to every word being built; whitespace ends a word."""
    done = []
    for part in re.split(r"(\s+)", text):
        if part.isspace():
            done += words
            words = [("", True)]
        elif part:
            words = [(w + part, exact) for w, exact in words]
    return words, done


def _class_words(src):
    """(word, exact) for every class a template writes, Jinja and all.

    A word glued to a value that cannot be read from here is a PREFIX, with
    exact False: status-{{ row.state }} is some class starting with status-.
    Markup inside a <script> is the script's business, not the page's."""
    out = []
    markup = re.sub(r"<script\b[^>]*>.*?</script>", "", src, flags=re.S | re.I)
    for attr in re.findall(r'\bclass="([^"]*)"', markup):
        words, done = [("", True)], []
        for piece in re.split(r"(\{\{.*?\}\}|\{%.*?%\})", attr, flags=re.S):
            if piece.startswith("{%"):
                done += words
                words = [("", True)]
            elif piece.startswith("{{"):
                grown = []
                for branch in _jinja_branches(piece[2:-2].strip("-")):
                    if branch is None:
                        grown += [(w, False) for w, _e in words]
                    else:
                        more, finished = _feed(words, branch)
                        grown += more
                        done += finished
                words = list(dict.fromkeys(grown))
            else:
                words, finished = _feed(words, piece)
                done += finished
        out += [(w, exact) for w, exact in dict.fromkeys(done + words)
                if w and _IDENT.match(w)]
    return out


def _script_classes(js):
    """(selected, added): the classes a script finds elements by, and the
    ones it puts on them."""
    selected, added = set(), set()
    for _q, lit in re.findall(r"(['\"`])((?:(?!\1).)*?)\1", js):
        # A selector handed to querySelector, closest or matches -- not a
        # file name or an address, whose dots are not classes.
        if "." in lit and not re.search(r"://|^/|@|\.(png|jpe?g|svg|css|js|html|pdf|ics)\b", lit):
            selected |= set(re.findall(r"\.(-?[a-zA-Z_][\w-]*)", lit))
    for m in re.finditer(r"classList\.(add|remove|toggle|contains|replace)\(([^)]*)\)", js):
        args = [a.strip() for a in m.group(2).split(",")]
        names = [a[1:-1] for a in args if re.fullmatch(r"(['\"])[\w -]*\1", a)]
        if m.group(1) == "toggle":
            # toggle(name, condition): a string in the condition, like 'done'
            # in t.status === 'done', is not a class.
            names = names[:1] if args and re.fullmatch(r"(['\"])[\w -]*\1", args[0]) else []
        into = selected if m.group(1) in ("remove", "contains") else added
        into |= {c for n in names for c in n.split()}
    for m in re.finditer(r"className\s*\+?=\s*([^;\n]*)", js):
        rhs = m.group(1)
        for lit in re.finditer(r"(['\"])([^'\"]*)\1", rhs):
            if re.search(r"[=!]==?\s*$", rhs[:lit.start()]) or re.match(r"\s*[=!]==?", rhs[lit.end():]):
                continue
            added |= {c for c in lit.group(2).split() if _IDENT.match(c)}
    for m in re.finditer(r"getElementsByClassName\((['\"])([^'\"]*)\1", js):
        selected |= set(m.group(2).split())
    return selected, added


def _css_selectors(css):
    """Every selector in a stylesheet, inside @media and all, one per comma."""
    css = re.sub(r"url\([^)]*\)", "url()", _strip_comments(css))
    out = []
    for prelude in re.findall(r"([^{};]+)\{", css):
        prelude = prelude.strip()
        if not prelude or prelude.startswith("@"):
            continue
        depth, cur = 0, ""
        for ch in prelude:
            depth += ch in "(["
            depth -= ch in ")]"
            if ch == "," and depth == 0:
                out.append(cur.strip())
                cur = ""
            else:
                cur += ch
        out.append(cur.strip())
    return [sel for sel in out if sel]


def _selector_classes(sel):
    sel = re.sub(r"(['\"]).*?\1", "", re.sub(r"\[[^\]]*\]", "", sel))
    return set(re.findall(r"\.(-?[a-zA-Z_][\w-]*)", sel))


def _selector_needs(sel):
    """The classes an element and its ancestors must carry for the selector
    to match at all. Inside :not() must be ABSENT and :is() and :where()
    offer alternatives, so neither is a requirement."""
    prev, s = None, re.sub(r"\[[^\]]*\]", "", sel)
    while prev != s:
        prev = s
        s = re.sub(r":(?:not|is|where|matches|-webkit-any)\((?:[^()]|\([^()]*\))*\)", "", s)
    return _selector_classes(s)


class _Drawn:
    """One stylesheet, as the classes its rules name and what each rule needs."""

    def __init__(self, css):
        self.needs = {}
        for sel in _css_selectors(css):
            need = _selector_needs(sel)
            for c in _selector_classes(sel):
                self.needs.setdefault(c, []).append(need)

    def draws(self, word, exact, present, prefixes):
        def reachable(need):
            return all(n in present or any(n.startswith(p) for p in prefixes)
                       for n in need)
        if exact:
            return any(reachable(n) for n in self.needs.get(word, ()))
        return any(c.startswith(word) and any(reachable(n) for n in alts)
                   for c, alts in self.needs.items())


def staff_class_audit(srcs, stylesheets, named=()):
    """(findings, pages read, class uses read) for one set of templates.

    srcs is {template name: source}, stylesheets is {file name in static/:
    css}. A finding is (page, the template that writes it, the class), with a
    * on a prefix. Kept apart from run() so the same sweep can be put to a
    set of templates that is known to be wrong, and seen to come out wrong.
    """
    srcs = {n: re.sub(r"\{#.*?#\}", "", s, flags=re.S) for n, s in srcs.items()}
    sheets = {n: _Drawn(css) for n, css in stylesheets.items()}
    blocks, words, scripts = {}, {}, {}

    def chain(n):
        out = []
        while n and n in srcs and n not in out:
            out.append(n)
            n = _template_parent(srcs[n])
        return out

    def members(page):
        todo, seen = chain(page), set()
        while todo:
            t = todo.pop()
            if t in seen or t not in srcs:
                continue
            seen.add(t)
            todo += list(_template_uses(srcs[t])) + chain(t)
        return seen

    findings, pages, uses = [], 0, 0
    for page in sorted(srcs):
        if page in ("base.html", "public_base.html", "pos_base.html"):
            continue
        if not _is_page(srcs[page]):
            continue                        # a partial: read with the pages that use it
        if chain(page)[-1] == "public_base.html":
            continue                        # drawn with gudanes.css, and the design side's
        pages += 1
        mem = members(page)
        drawn_by = [sheets[f] for t in mem for f in _linked_stylesheets(srcs[t]) if f in sheets]
        for t in mem:
            if t not in blocks:
                blocks[t] = _Drawn(_style_blocks(srcs[t])) if "<style" in srcs[t] else None
                words[t] = _class_words(srcs[t])
                scripts[t] = _script_classes(_inline_scripts(srcs[t]))
            if blocks[t] is not None:
                drawn_by.append(blocks[t])
        selected = {c for t in mem for c in scripts[t][0]}
        added = {c for t in mem for c in scripts[t][1]}
        present = {w for t in mem for w, exact in words[t] if exact} | added
        prefixes = {w for t in mem for w, exact in words[t] if not exact}
        for t in sorted(mem):
            for w, exact in sorted(set(words[t])):
                uses += 1
                if (exact and w in selected) or w in named:
                    continue
                if not any(d.draws(w, exact, present, prefixes) for d in drawn_by):
                    findings.append((page, t, w if exact else w + "*"))
        for w in sorted(added - set(named)):
            if not any(w in d.needs for d in drawn_by):
                findings.append((page, "a script", w))
    return findings, pages, uses


def _element_rule_needs(css, tag):
    """What each rule that draws a bare <tag> needs: the classes its
    ancestors must carry. A selector ending in h2 counts, .card-course h2 or
    a plain h2; one ending in h2.x or .x does not, since it asks for a class
    the bare tag has not got."""
    out = []
    for sel in _css_selectors(css):
        subject = re.split(r"\s*[\s>+~]\s*", sel.strip())[-1]
        if re.fullmatch(r"%s(?:::?[\w-]+(?:\([^)]*\))?)*" % tag, subject):
            out.append(_selector_needs(sel))
    return out


def staff_bare_h2_audit(srcs, stylesheets):
    """[(page, template, line)] for every <h2> with no class on a staff page
    that no rule it loads draws as an element.

    The house's section heading is a class, .section-heading, so a bare h2 is
    drawn by the browser alone: 24px bold Inter, larger than and unlike every
    other heading on the staff side. A page that styles its own h2 -- the
    printed menu's course titles, the office display, the Outlook pane, the
    till's order head -- is drawn and left alone. Per page, as the class
    audit is: a page with one such rule clears every bare h2 on it."""
    # Blanked rather than cut, so a line number still points into the file.
    def blank(m):
        return "\n" * m.group(0).count("\n")
    srcs = {n: re.sub(r"\{#.*?#\}", blank, s, flags=re.S) for n, s in srcs.items()}
    sheet_rules = {n: _element_rule_needs(_strip_comments(css), "h2")
                   for n, css in stylesheets.items()}
    out = []
    for page in sorted(srcs):
        if page in ("base.html", "public_base.html", "pos_base.html"):
            continue
        if not (_template_parent(srcs[page]) or re.search(r"<html\b", srcs[page], re.I)):
            continue
        chain, t = [], page
        while t and t in srcs and t not in chain:
            chain.append(t)
            t = _template_parent(srcs[t])
        if chain[-1] == "public_base.html":
            continue
        mem, todo = set(), list(chain)
        while todo:
            t = todo.pop()
            if t not in mem and t in srcs:
                mem.add(t)
                todo += list(_template_uses(srcs[t])) + [p for p in [_template_parent(srcs[t])] if p]
        rules = [need for t in mem for f in _linked_stylesheets(srcs[t])
                 for need in sheet_rules.get(f, ())]
        rules += [need for t in mem if "<style" in srcs[t]
                  for need in _element_rule_needs(_strip_comments(_style_blocks(srcs[t])), "h2")]
        present = {w for t in mem for w, exact in _class_words(srcs[t]) if exact}
        if any(need <= present for need in rules):
            continue
        for t in sorted(mem):
            markup = re.sub(r"<script\b[^>]*>.*?</script>", blank, srcs[t], flags=re.S | re.I)
            for m in re.finditer(r"<h2\b([^>]*)>", markup, re.I):
                if not re.search(r"\bclass\s*=", m.group(1)):
                    out.append((page, t, markup.count("\n", 0, m.start()) + 1))
    return out


def _tokens_defined(css):
    return set(re.findall(r"(--[\w-]+)\s*:", css))


def _tokens_read_bare(css):
    """Custom properties read with no fallback: var(--x), not var(--x, 12px).
    With no value behind one, the declaration is dropped when it is worked
    out, and the colour or the gap it was for is simply not there."""
    return set(re.findall(r"var\(\s*(--[\w-]+)\s*\)", css))


def staff_token_audit(srcs, stylesheets):
    """[(page, template, token)] for every token a staff page's own styles read
    that no stylesheet it loads, and none of its own styles, defines."""
    srcs = {n: re.sub(r"\{#.*?#\}", "", s, flags=re.S) for n, s in srcs.items()}
    defined = {n: _tokens_defined(_strip_comments(css)) for n, css in stylesheets.items()}

    def own(src):
        return _style_blocks(src) + "\n" + "\n".join(re.findall(r'\bstyle="([^"]*)"', src))

    out = []
    for page in sorted(srcs):
        if page in ("base.html", "public_base.html", "pos_base.html"):
            continue
        if not _is_page(srcs[page]):
            continue
        chain, t = [], page
        while t and t in srcs and t not in chain:
            chain.append(t)
            t = _template_parent(srcs[t])
        if chain[-1] == "public_base.html":
            continue
        mem, todo = set(), list(chain)
        while todo:
            t = todo.pop()
            if t not in mem and t in srcs:
                mem.add(t)
                todo += list(_template_uses(srcs[t]))
        have = set()
        for t in mem:
            have |= _tokens_defined(own(srcs[t]))
            for f in _linked_stylesheets(srcs[t]):
                have |= defined.get(f, set())
        for t in sorted(mem):
            out += [(page, t, tok) for tok in sorted(_tokens_read_bare(own(srcs[t])) - have)]
    return out


def run():
    s = Suite("Design")
    css = _strip_comments(open(CSS_PATH, encoding="utf-8").read())
    rules = _rules(css)

    declared = {}
    for selector_group, body in rules:
        for selector in selector_group.split(","):
            selector = selector.strip()
            if not selector or selector.startswith("@"):
                continue
            declared.setdefault(selector, {}).update(_declarations(body))

    s.section("Variant classes a shell rule would override")
    co_occurring = _co_occurring_classes()
    shell_rules = {sel: props for sel, props in declared.items()
                   if sel.startswith("body.staff-shell .") and len(sel.split()) == 2}
    losers = []
    for shell_sel, shell_props in shell_rules.items():
        base = shell_sel.split()[-1]
        for sel, props in declared.items():
            if not sel.startswith(base + "-") or " " in sel:
                continue
            # A name that merely starts with the base is not a variant:
            # .sidebar-toggle is its own element, never on the same tag as
            # .sidebar. Only a pair the markup actually puts together can clash.
            if (base.lstrip("."), sel.lstrip(".")) not in co_occurring:
                continue
            clashing = [p for p in set(props) & set(shell_props) if not props[p]]
            if not clashing or specificity(sel) >= specificity(shell_sel):
                continue
            # A later rule may already restore it at higher specificity —
            # that is how the fixed cases were fixed. A state-only rule does
            # not count: `.btn-mini.btn-mini-active:hover` restores the colour
            # under the cursor and nowhere else, which is precisely the bug.
            def restores(fix_sel, fix_props):
                if ":" in fix_sel and ":" not in sel:
                    return False
                return (base in fix_sel and sel.lstrip(".") in fix_sel
                        and specificity(fix_sel) > specificity(shell_sel)
                        and set(clashing) & set(fix_props))

            if any(restores(f, p) for f, p in declared.items()):
                continue
            losers.append(f"{sel} loses {','.join(sorted(clashing))} to {shell_sel}")
    s.check("no variant is silently overridden by the staff shell", not losers,
            detail=" | ".join(losers[:4]))

    s.section("Text contrast (WCAG AA, 4.5:1)")
    tokens = _tokens(css)
    root, staff = tokens.get("root", {}), tokens.get("staff", {})
    # Pairs that have regressed before, checked in the shell they belong to.
    pairs = [
        ("--ink", "--parchment", "root", "body text on the public shell"),
        ("--ink", "--ivory", "root", "body text on cards"),
        ("--gold-on-warn", "--warn-bg", "staff", "warning badge text"),
        ("--gold-on-warn", "--warn-bg", "root", "warning badge text"),
    ]
    misses = []
    checked = 0
    for fg_name, bg_name, scope, label in pairs:
        palette = staff if scope == "staff" else root
        fg, bg = palette.get(fg_name) or root.get(fg_name), palette.get(bg_name) or root.get(bg_name)
        if not fg or not bg:
            continue
        checked += 1
        ratio = contrast(fg, bg)
        if ratio < AA_NORMAL:
            misses.append(f"{label} ({scope}) {ratio:.2f}:1")
    s.check(f"{checked} known token pairs meet AA", not misses, detail=" | ".join(misses))

    s.section("Opacity on text rules")
    # Both sub-AA failures found by hand came from an opacity on an otherwise
    # correct colour, which no colour-pair check can see.
    #
    # Decorative glyphs are exempt, and must say so in the markup with
    # aria-hidden — an element that carries no information for a screen reader
    # is not carrying any for a sighted reader either, so it may recede.
    decorative = _aria_hidden_classes()
    faded = []
    for selector_group, body in rules:
        decls = _declarations(body)
        if "opacity" not in decls or "color" not in decls:
            continue
        if all(sel.strip().lstrip(".") in decorative
               for sel in selector_group.split(",") if sel.strip()):
            continue
        match = re.search(r"opacity\s*:\s*([0-9.]+)", body)
        if match and float(match.group(1)) < 0.75:
            faded.append(f"{selector_group.strip()[:44]} @ {match.group(1)}")
    s.check("no coloured text is faded below 0.75 opacity", not faded,
            detail=" | ".join(faded[:4]))

    s.section("Markup uses classes the stylesheet defines")
    import glob
    all_css = "\n".join(
        _strip_comments(open(path, encoding="utf-8", errors="replace").read())
        for path in ALL_CSS)
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", all_css))
    used = {}
    for path in glob.glob(os.path.join(ROOT, "templates", "*.html")):
        html = open(path, encoding="utf-8").read()
        # Templates carry their own <style> blocks in a couple of places.
        local = set(re.findall(r"\.([a-zA-Z][\w-]*)",
                               " ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))))
        for attr in re.findall(r'class="([^"]*)"', html):
            cleaned = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", attr)
            for cls in cleaned.split():
                if re.fullmatch(r"[a-zA-Z][\w-]*", cls) and cls not in defined and cls not in local:
                    used.setdefault(cls, set()).add(os.path.basename(path))
    # A layout class with no rule is the bug worth catching: `.table-wrap` was
    # used by thirteen templates with no CSS at all, so wide tables pushed the
    # page sideways on a phone instead of scrolling inside their wrapper.
    layoutish = {c: f for c, f in used.items()
                 if re.search(r"wrap|bar|grid|row|col|stack|panel|card|table", c)}
    s.check("no layout class is used without a rule", not layoutish,
            detail=" | ".join(f".{c} in {len(f)} file(s)" for c, f in list(layoutish.items())[:4]))
    if used and not layoutish:
        print(f"    ....  {len(used)} non-layout classes have no rule "
              "(mostly Jinja-built names — not checked)")

    s.section("A staff page uses only classes the stylesheets it loads can draw")
    srcs = {os.path.basename(p): open(p, encoding="utf-8").read()
            for p in glob.glob(os.path.join(ROOT, "templates", "*.html"))}
    sheets = {os.path.basename(p): open(p, encoding="utf-8", errors="replace").read()
              for p in ALL_CSS}
    found, pages, uses = staff_class_audit(srcs, sheets)
    # Counted, so a sweep that stopped finding pages would say so rather than
    # pass on nothing.
    s.check("the sweep reads %d staff pages and %d classes on them" % (pages, uses),
            pages > 250 and uses > 8000,
            detail="too few to be reading the templates at all")
    undrawn = sorted({(t, c) for _page, t, c in found if c not in NAMED_NOT_DRAWN})
    s.check("every class on a staff page is drawn by a stylesheet that page loads",
            not undrawn,
            detail="%d, e.g. %s -- a staff page loads style.css alone, so a g- class "
                   "is the public stylesheet's. The staff ones: .cell-note under a figure, "
                   ".field-hint under a field, .upload-hint, .muted, .page-intro, "
                   ".empty-inline. Or give it a rule, or say why none in NAMED_NOT_DRAWN"
                   % (len(undrawn), ["%s .%s" % tc for tc in undrawn[:5]]))
    stale = sorted(set(NAMED_NOT_DRAWN) - {c for _p, _t, c in found})
    s.check("and every name on the list no rule draws is still used and still undrawn",
            not stale,
            detail="take these off NAMED_NOT_DRAWN: %s -- a list that outlives its "
                   "reason is how the next one gets in unnoticed" % stale)

    s.section("And the sweep can come out wrong")
    # Put to templates that are known to be right or wrong in one way each.
    # A sweep that cannot fail on these is not asking anything.
    shell = ("<!DOCTYPE html><html><head><link rel=\"stylesheet\" href=\"{{ url_for('static', "
             "filename='%s') }}\"></head><body class=\"staff-shell\">{%% block content %%}"
             "{%% endblock %%}</body></html>")
    fixture_css = {
        "style.css": ".staff-shell{} .muted{} .card .inner{} .status-late{} .role-owner{} .shown{}",
        "gudanes.css": ".g-hint{}",
    }

    def verdict(body, base="base.html", extra=None):
        t = {"base.html": shell % "style.css", "public_base.html": shell % "gudanes.css",
             "page.html": '{%% extends "%s" %%}{%% block content %%}%s{%% endblock %%}'
                          % (base, body)}
        t.update(extra or {})
        return [c for _p, _t, c in staff_class_audit(t, fixture_css)[0]]

    link_public = ("<link rel=\"stylesheet\" href=\"{{ url_for('static', "
                   "filename='gudanes.css') }}\">")
    cases = [
        ("a class only the public stylesheet draws is caught on a staff page",
         verdict('<p class="g-hint">x</p>'), ["g-hint"]),
        ("and passes on a staff page that links the public stylesheet itself",
         verdict(link_public + '<p class="g-hint">x</p>'), []),
        ("a public page is left alone: it is the design side's, drawn with its own",
         verdict('<p class="g-hint">x</p>', base="public_base.html"), []),
        ("a class nothing draws at all is caught",
         verdict('<p class="muted">x</p><p class="empty-note">y</p>'), ["empty-note"]),
        ("a class whose only rule needs an ancestor the page lacks is caught",
         verdict('<span class="inner">x</span>'), ["inner"]),
        ("and is drawn once that ancestor is on the page",
         verdict('<div class="card"><span class="inner">x</span></div>'), []),
        ("a class a script finds elements by is a handle and needs no rule",
         verdict('<i class="hook"></i><script>document.querySelector(".hook")</script>'), []),
        ("a class a script adds needs one",
         verdict('<script>document.body.classList.add("is-stale");'
                 'document.body.classList.add("shown")</script>'), ["is-stale"]),
        ("a Jinja branch is read as the class it prints, not the string it compares",
         verdict('<b class="status-{{ \'late\' if a == \'admin_hr\' else \'early\' }}">x</b>'),
         ["status-early"]),
        ("a class built from a value is a prefix, drawn if a rule starts with it",
         verdict('<b class="role-{{ user.role }} kind-{{ x }}">x</b>'), ["kind-*"]),
        ("a partial is read on the staff page that includes it",
         verdict('{% include "_bit.html" %}',
                 extra={"_bit.html": '<p class="nothing-draws-this">x</p>'}),
         ["nothing-draws-this"]),
    ]
    for label, got, expected in cases:
        s.check(label, got == expected, detail="expected %s, got %s" % (expected, got))

    s.section("A section heading on a staff page wears the house's class")
    # Sixty-odd <h2> on twenty-five staff pages had no class at all, so the
    # browser drew them: 24px bold Inter, a size larger than the Playfair
    # .section-heading every other staff section opens with. Nothing reported
    # it -- the class audit reads classes, and a tag with none has none to read.
    bare = staff_bare_h2_audit(srcs, sheets)
    s.check("no staff page has an <h2> without a class that nothing on it draws",
            not bare,
            detail="%d, e.g. %s -- write <h2 class=\"section-heading\">, keeping any "
                   "inline margin" % (len(bare), ["%s:%d" % (t, n) for _p, t, n in bare[:6]]))
    h2_shells = {"base.html": shell % "style.css", "public_base.html": shell % "gudanes.css"}

    def bare_h2(body, base="base.html", extra=None, css=None):
        t = dict(h2_shells)
        t["page.html"] = ('{%% extends "%s" %%}{%% block content %%}%s{%% endblock %%}'
                          % (base, body))
        t.update(extra or {})
        return [(tpl, n) for _p, tpl, n in
                staff_bare_h2_audit(t, css or {"style.css": ".section-heading{}",
                                               "gudanes.css": "h2{}"})]

    h2_cases = [
        ("a bare h2 on a staff page is caught, at its line",
         bare_h2('<p>x</p>\n<h2>Who</h2>'), [("page.html", 2)]),
        ("and one with the house's class is not",
         bare_h2('<h2 class="section-heading" style="margin-top:0;">Who</h2>'), []),
        ("an inline style is not a class",
         bare_h2('<h2 style="margin-top:28px;">Who</h2>'), [("page.html", 1)]),
        ("a public page is left alone: gudanes.css draws its h2",
         bare_h2('<h2>Who</h2>', base="public_base.html"), []),
        ("a page whose own style draws its h2 is left alone",
         bare_h2('<div class="course"><h2>Starters</h2></div>'
                 '<style>.course h2{ font-size:12px; }</style>'), []),
        ("but not when the rule needs an ancestor the page lacks",
         bare_h2('<h2>Starters</h2><style>.course h2{ font-size:12px; }</style>'),
         [("page.html", 1)]),
        ("nor when the rule asks for a class the tag has not got",
         bare_h2('<h2>Starters</h2><style>h2.course{ font-size:12px; }</style>'),
         [("page.html", 1)]),
        ("a stylesheet that draws a bare h2 clears it",
         bare_h2('<h2>Who</h2>', css={"style.css": "h1, h2{ margin:0 }", "gudanes.css": ""}), []),
        ("a bare h2 in a partial is caught where it is written",
         bare_h2('{% include "_bit.html" %}', extra={"_bit.html": "\n\n<h2>Who</h2>"}),
         [("_bit.html", 3)]),
        ("one in a Jinja comment or a script is not markup",
         bare_h2('{# <h2>old</h2> #}<script>el.innerHTML = "<h2>x</h2>";</script>'), []),
    ]
    for label, got, expected in h2_cases:
        s.check(label, got == expected, detail="expected %s, got %s" % (expected, got))

    s.section("A staff page reads only colours and spacings that are defined for it")
    # The same fault in a second form. --rust was read by style.css and six
    # staff templates and defined by nothing, so "never cleaned" and "short of
    # stock" printed in plain ink; --s3 is the public stylesheet's, so the
    # shopping row's gap and the arrival card's spacing were not there.
    staff_css = _strip_comments(sheets["style.css"])
    loose = sorted(_tokens_read_bare(staff_css) - _tokens_defined(staff_css))
    s.check("every token style.css reads without a fallback, style.css defines",
            not loose, detail="read and never defined: %s" % loose)
    loose_pages = sorted({(t, tok) for _p, t, tok in staff_token_audit(srcs, sheets)})
    s.check("and so does every staff page's own style, or a stylesheet it loads",
            not loose_pages, detail="%d, e.g. %s" % (len(loose_pages), loose_pages[:5]))
    probe = staff_token_audit(
        {"base.html": shell % "style.css", "public_base.html": shell % "gudanes.css",
         "a.html": '{% extends "base.html" %}{% block content %}<p style="margin:var(--s9)">'
                   '<b style="gap:var(--s1)"><i style="color:var(--nope, red)">x</i></b></p>'
                   '{% endblock %}',
         "b.html": '{% extends "public_base.html" %}{% block content %}'
                   '<p style="margin:var(--s9)">x</p>{% endblock %}'},
        {"style.css": ":root{ --s1:4px; }", "gudanes.css": ":root{ --s9:96px; }"})
    s.check("and the sweep can tell: an undefined token is caught, a defined one or "
            "one with a fallback is not, and a public page is left alone",
            [tok for _p, _t, tok in probe] == ["--s9"], detail="got %s" % probe)

    s.section("A media query the browser can actually evaluate")

    # The PUBLIC stylesheet. CSS_PATH above is style.css, which is the staff
    # app's -- and pointing these at it made every one of them pass by looking
    # at a file that has none of this in it, which is the most comfortable way
    # for a check to be useless.
    public = _strip_comments(
        open(os.path.join(os.path.dirname(CSS_PATH), "gudanes.css"),
             encoding="utf-8").read())

    # A CUSTOM PROPERTY CANNOT BE USED IN A MEDIA QUERY CONDITION. They are
    # resolved at computed-value time, long after the query has been decided,
    # so `@media (max-width: var(--m-read))` is discarded whole -- and the
    # forty-two blocks written that way had never once applied. The navigation
    # label that was meant to disappear below 26rem stayed on screen at 320px,
    # the back-to-top never took its phone position, and nothing reported any
    # of it, because a stylesheet that fails to parse a block simply drops it.
    #
    # The variables are fine everywhere else. This is only about the condition.
    dead = re.findall(r"@media[^{]*var\(--[^)]*\)", public)
    s.check("no media query is written with a custom property in it", not dead,
            detail="; ".join(d.strip()[:60] for d in dead[:3]))

    # EVERY BLOCK IS CLOSED, which sounds like something a stylesheet cannot
    # get wrong and is the second way this file has silently lost rules.
    #
    # A missing `}` does not break the page. The rule simply swallows
    # everything after it: the declarations become part of the unclosed
    # selector, the selectors after it become invalid declarations, and the
    # browser drops them without a word. It happened on a merge -- a conflict
    # boundary fell inside .g-care__note and the closing brace went with it --
    # and from then on the approach map's phone variant and the road notice
    # were both dead. Every other check here passed, because they read the
    # TEXT of this file and the text was all present.
    depth, opened_at = 0, []
    for line_no, line in enumerate(public.split("\n"), 1):
        for ch in line:
            if ch == "{":
                depth += 1
                opened_at.append(line_no)
            elif ch == "}":
                depth -= 1
                if opened_at:
                    opened_at.pop()
    s.check("every block in the public stylesheet is closed", depth == 0,
            detail="%d unclosed, first opened near line %s — an unclosed rule "
                   "swallows every rule after it and the browser says nothing"
                   % (depth, opened_at[0] if opened_at else "?"))
    s.check("and the breakpoints are still declared for ordinary use",
            "--m-tight:" in public and "--m-read:" in public,
            detail="they work in a declaration; only the condition is the problem")

    s.section("The furniture on a phone")

    # Both found by scrolling a 390px screen rather than looking at the top of
    # it: the menu button ran off the right edge, and the back-to-top arrow sat
    # exactly where the booking bar puts its button -- covering the one control
    # on the page that has to work.
    s.check("the wordmark has a floor so it cannot squeeze to nothing",
            re.search(r"\.g-logo\s*\{[^}]*min-width:\s*\d", public),
            detail="it was allowed to absorb all the pressure and went to 34px")
    s.check("and a ceiling so it cannot crowd out the menu",
            re.search(r"\.g-logo\s*\{[^}]*max-width:", public))
    # ASKED AS THE PROPERTY, NOT THE MECHANISM. This used to require a `left:`
    # in the base rule, because moving the arrow leftwards was how it was got
    # out of the Book button's corner at the time. A handover raised it on
    # small screens instead, which clears the bar just as well and keeps the
    # arrow where a thumb reaches for it -- and this went red for a page that
    # was fine. What must stay true is that on a phone the arrow is not left
    # at its default offset in the same corner as Book: lifted, moved aside or
    # taken away, any of the three will do.
    small_screen = re.findall(
        r"@media[^{]*max-width[^{]*\{(?:[^{}]|\{[^{}]*\})*?\.g-totop\s*\{([^}]*)\}",
        public)
    s.check("the back-to-top is kept out of the booking bar's corner on a phone",
            any(re.search(r"(bottom:\s*\d|display:\s*none|left:)", rule)
                for rule in small_screen),
            detail="pinned bottom-right at the default offset it covers Book, "
                   "which is the one control on the page that has to work: "
                   + (str(small_screen[:2]) if small_screen
                      else "no small-screen rule for .g-totop at all"))

    # AND AS FAR AS THE BAR REACHES, NOT JUST SOMEWHERE ON A PHONE. The check
    # above asks whether any small-screen rule lifts, moves or hides the
    # arrow, and it was green while the arrow sat on Book between 704px and
    # 960px on the live site: the hide stopped at 44rem, and the bar is drawn
    # to 60rem (.g-stickybar) and 61.99rem (.g-bookbar, the room page).
    # Measured at 820px on What's On and on the room page, both covered. So
    # this asks the second half of the question: for each bar, is the arrow
    # kept off it up to the widest width that bar is drawn at?
    MQ = r"@media\s*\(max-width:\s*([\d.]+)rem\)\s*\{(.*?)\}\s*\}"
    def widest_bar(cls):
        best = 0.0
        for mq in re.finditer(MQ, public, re.S):
            if re.search(re.escape(cls) + r"\s*\{[^}]*display:\s*(flex|block|grid)",
                         mq.group(2)):
                best = max(best, float(mq.group(1)))
        return best
    def arrow_hidden_to(cls):
        best = 0.0
        for mq in re.finditer(MQ, public, re.S):
            if re.search(r"body:has\(" + re.escape(cls)
                         + r"\)\s+\.g-totop[^{]*\{[^}]*display:\s*none", mq.group(2)):
                best = max(best, float(mq.group(1)))
        return best
    for bar in (".g-stickybar", ".g-bookbar"):
        reach, hidden = widest_bar(bar), arrow_hidden_to(bar)
        s.check("the back-to-top is out of the way wherever %s is drawn" % bar,
                reach and hidden >= reach,
                detail="%s is drawn up to %.2frem and the arrow is only kept off "
                       "it up to %.2frem, so in between it sits on Book"
                       % (bar, reach, hidden))

    s.section("A phone carousel starts at its first card")
    # On phones the room cards on Stay and the cards on The Estate become a
    # sideways scroller, and .g-cards was centred. Centring a row that is
    # wider than its container pushes half the overflow off the LEFT edge,
    # and a browser cannot scroll to negative space -- so the first card sat
    # at x = -667 and Chambre Emeraude could never be seen on the Stay page
    # at all, nor La Piscine on The Estate. Nothing overflowed the DOCUMENT,
    # which is why a sweep for sideways page-scroll passed it clean: the
    # missing cards were inside a scroller, off its unreachable side.
    #
    # Checked on the source because stale exports have reverted this
    # stylesheet three times running, and a carousel that silently loses its
    # first two rooms looks perfectly fine to anybody who does not count them.
    # THE LAST ONE WINS, so that is the one read. The stylesheet has thirty-
    # five phone-width blocks; the first version of this matched the first
    # of them, found no .g-cards in it, and failed against a file that was
    # already correct.
    phone_says = []
    for block in re.finditer(
            r"@media\s*\(max-width:\s*34rem\)\s*\{(.*?)\}\s*\}", public, re.S):
        for rule in re.finditer(
                r"\.g-cards[^{]*\{[^}]*?justify-content:\s*([a-z ]+?)\s*;",
                block.group(1)):
            phone_says.append(rule.group(1).strip())
    s.check("at phone width the card row justifies from the start",
            phone_says and phone_says[-1] == "start",
            detail="phone-width .g-cards rules, in order: %s -- centring an "
                   "overflowing scroller hides its first cards off the left "
                   "edge, where nothing can scroll to them" % phone_says)
    s.check("and elsewhere it centres only when it can do so safely",
            re.search(r"\.g-cards[^{]*\{[^}]*justify-content:\s*safe\s+center",
                      public),
            detail="`safe center` falls back to start the moment the row "
                   "would overflow; plain `center` does not")

    return s
