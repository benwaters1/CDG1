"""A loop variable that shadows a template global, and then calls it.

staff_today looped `{% for t in my_tasks %}` and, inside that loop, called
t('urgent') — which is the translation function everywhere else in the app and
a sqlite3.Row in there. Jinja does not complain; it raises at render.

It only fired when somebody had a HIGH priority task, and almost nothing in
the app is high priority. The turnover checklist and the arrival prep are. So
the page every employee opens in the morning worked perfectly until the first
changeover day, and then five-hundredth for whoever the work was given to.

Twenty-five templates loop `t`. None of the others calls it inside today, so
none of them is broken — every one of them is a landmine for the next person
who adds a translated string inside a loop, and that is the thing this checks.
Not "does a template shadow a name", which would flag twenty-five files that
work; only "does it shadow a name and then use it as a function", which is the
shape that actually fails.
"""
import os
import re

from _harness import Suite
import _harness

TEMPLATES = os.path.join(_harness.ROOT, "templates")

# Names the app puts in every template's namespace. A loop variable with one
# of these names hides it for the length of the loop.
TEMPLATE_GLOBALS = {"t", "url_for", "csrf_token", "session", "request",
                    "icon", "emblem", "mark", "get_flashed_messages"}


def _loops(src):
    """(variable, body) for every for-loop, body up to its endfor."""
    for mo in re.finditer(r"\{%-?\s*for\s+([a-zA-Z_]\w*)(?:\s*,\s*\w+)?\s+in\s", src):
        var = mo.group(1)
        end = src.find("{% endfor %}", mo.end())
        body = src[mo.end():end if end != -1 else len(src)]
        yield var, body


def run():
    s = Suite("Shadowed template globals")

    names = [n for n in sorted(os.listdir(TEMPLATES))
             if n.endswith((".html", ".xml", ".js"))]
    s.check(f"there are {len(names)} templates to check", len(names) > 50)

    s.section("Nothing shadows a global and then calls it")
    broken, shadowing = [], 0
    for name in names:
        with open(os.path.join(TEMPLATES, name), encoding="utf-8") as fh:
            src = fh.read()
        for var, body in _loops(src):
            if var not in TEMPLATE_GLOBALS:
                continue
            shadowing += 1
            # Called as a function inside the loop that hid it. This is the
            # only shape that raises.
            if re.search(rf"\b{re.escape(var)}\s*\(", body):
                broken.append(f"{name}: loops '{var}' and calls {var}() inside it")

    s.check("no loop hides a function and then uses it", not broken,
            detail="; ".join(broken[:3]) if broken else
                   f"{shadowing} loop(s) shadow a global without calling it")

    s.section("The check can tell the two apart")
    # Without this it passes on everything the moment the pattern breaks, and
    # it would also be worthless if it flagged all twenty-five harmless ones.
    harmless = "{% for t in rows %}{{ t['name'] }}{% endfor %}"
    fatal = "{% for t in rows %}{{ t('Save') }}{% endfor %}"
    got_harmless = [v for v, b in _loops(harmless)
                    if v in TEMPLATE_GLOBALS and re.search(r"\bt\s*\(", b)]
    got_fatal = [v for v, b in _loops(fatal)
                 if v in TEMPLATE_GLOBALS and re.search(r"\bt\s*\(", b)]
    s.check("a loop that only reads the row is fine", not got_harmless,
            detail="twenty-five templates do this and none of them is broken")
    s.check("and one that calls the shadowed name is not", bool(got_fatal),
            detail="this is the exact line that five-hundredth the staff page")

    s.section("The one that was actually broken")
    with open(os.path.join(TEMPLATES, "staff_today.html"), encoding="utf-8") as fh:
        staff = fh.read()
    s.check("staff_today still translates something", "{{ t(" in staff,
            detail="if the fix had been to stop calling t() rather than to "
                   "rename the loop, this file would be silently untranslated")
    s.check("and its task loop no longer uses the name t",
            not any(v == "t" for v, _b in _loops(staff)),
            detail="the loop was renamed rather than the function, because t() "
                   "is used in every template and a task is the local thing")

    return s


if __name__ == "__main__":
    print(run().report())
