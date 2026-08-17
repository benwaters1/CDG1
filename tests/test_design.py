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
    # The public site loads a second stylesheet. Checking only style.css
    # reported every .g- class as undefined, which is a false alarm loud
    # enough to make the real ones easy to skip past.
    defined = set()
    for sheet in glob.glob(os.path.join(ROOT, "static", "*.css")):
        defined |= set(re.findall(r"\.([a-zA-Z][\w-]*)",
                                  _strip_comments(open(sheet, encoding="utf-8").read())))
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
    return s
