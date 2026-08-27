"""Every link in every template, resolved against the real url_map.

Three faults, all of which shipped to the live site in a single afternoon:

A `url_for()` naming an endpoint that does not exist raises BuildError when
the template renders. Because the public and staff shells are shared, one bad
name in a base template takes down EVERY page that extends it — the failure is
never local to the link.

A hardcoded `href="/thing"` fails more quietly: no exception, no log, just a
404 for whoever clicked it. `/restoration` and `/gallery` were linked from the
public header on every page for a while before their routes existed.

Merge conflict markers in a shipped template are the third. A merge that git
reports as successful can still leave `<<<<<<<` in a file nobody re-read, and
the page renders it to the guest.

None of this needs a browser: the url_map knows every real route, so the
answer is arithmetic rather than clicking. That is the point — clicking around
is exactly what missed all three.
"""
import os
import re

from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing.exceptions import RequestRedirect

from _harness import Suite, ROOT
import _harness

m = _harness.m
TPL_DIR = os.path.join(ROOT, "templates")

URL_FOR = re.compile(r"url_for\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")
# The whole call, arguments included, so the keywords can be checked against
# the route rather than only the endpoint name.
URL_FOR_CALL = re.compile(
    r"url_for\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]([^()]*)\)", re.S)
KWARG = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=")
# Only absolute paths. A relative href resolves against the current page, which
# a static check cannot judge.
HREF = re.compile(r"""(?:href|action)=["'](/[^"'#?{}]*)["']""")


def _templates():
    out = {}
    for dirpath, _dirs, files in os.walk(TPL_DIR):
        for f in files:
            if not f.endswith(".html"):
                continue
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, TPL_DIR).replace("\\", "/")
            with open(full, encoding="utf-8", errors="replace") as fh:
                out[rel] = fh.read()
    return out


def run():
    s = Suite("Links")
    templates = _templates()
    endpoints = {r.endpoint for r in m.app.url_map.iter_rules()}
    adapter = m.app.url_map.bind("localhost")

    s.section(f"{len(templates)} templates against {len(endpoints)} real endpoints")

    bad_endpoints, dead_paths, markers = [], [], []
    for rel, body in sorted(templates.items()):
        for lineno, line in enumerate(body.splitlines(), 1):
            for ep in URL_FOR.findall(line):
                if ep not in endpoints:
                    bad_endpoints.append(f"{rel}:{lineno} url_for('{ep}')")
            for path in HREF.findall(line):
                try:
                    # Matches parameterised rules too, so /book/<int:id> resolves.
                    adapter.match(path, method="GET")
                except NotFound:
                    dead_paths.append(f'{rel}:{lineno} "{path}"')
                except (MethodNotAllowed, RequestRedirect):
                    pass          # the route exists; wrong verb or a redirect
        if "<<<<<<<" in body or ">>>>>>>" in body:
            markers.append(rel)

    s.check("every url_for names a real endpoint", not bad_endpoints,
            detail=" | ".join(bad_endpoints[:4]))
    s.check("no template links a path nothing serves", not dead_paths,
            detail=" | ".join(dead_paths[:4]))
    s.check("no shipped template holds merge conflict markers", not markers,
            detail=" | ".join(markers[:4]))

    # An endpoint that exists but is called with the wrong parameter NAME is a
    # BuildError too, and the name check above sails straight past it. Not
    # hypothetical: a handover shipped url_for('manage_booking', token=...)
    # against a route taking manage_token, so every room booking confirmation
    # 500'd — the page a guest lands on immediately after paying. It was caught
    # only because another suite happens to render that page. Nothing checked
    # the parameters themselves, on any of the other 200-odd templates.
    #
    # An endpoint can have several rules with different parameters, so the test
    # is that AT LEAST ONE rule is satisfiable: all of its required arguments
    # are supplied. Anything extra becomes a query string, which is always fine.
    rules_for = {}
    for rule in m.app.url_map.iter_rules():
        rules_for.setdefault(rule.endpoint, []).append(rule.arguments)

    unbuildable = []
    for rel, body in sorted(templates.items()):
        for match in URL_FOR_CALL.finditer(body):
            endpoint, argtext = match.group(1), match.group(2)
            if endpoint not in rules_for:
                continue                      # already reported above
            given = set(KWARG.findall(argtext))
            if any(required <= given for required in rules_for[endpoint]):
                continue
            lineno = body.count(chr(10), 0, match.start()) + 1
            wants = " or ".join(
                "{" + ", ".join(sorted(r)) + "}" for r in rules_for[endpoint])
            shown = ", ".join(sorted(given)) or "nothing"
            unbuildable.append(
                f"{rel}:{lineno} url_for('{endpoint}') given {shown}, wants {wants}")
    s.check("every url_for supplies the parameters its route needs",
            not unbuildable, detail=" | ".join(unbuildable[:4]))

    # render_template targets that are not on disk — a 500 on that route only,
    # but silent until someone visits it.
    with open(os.path.join(ROOT, "app.py"), encoding="utf-8", errors="replace") as fh:
        app_src = fh.read()
    missing = []
    for lineno, line in enumerate(app_src.splitlines(), 1):
        for name in re.findall(r"render_template\(\s*['\"]([^'\"]+\.html)['\"]", line):
            if name not in templates:
                missing.append(f"app.py:{lineno} '{name}'")
    s.check("every render_template target exists", not missing,
            detail=" | ".join(missing[:4]))
    return s
