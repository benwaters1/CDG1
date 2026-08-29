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
import ast
import builtins
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

    # A route that reads a name nothing ever defines raises NameError the
    # moment somebody opens it — and only then. guest_account referred to a
    # `restaurant_settings` that was never assigned in the function, so every
    # guest who asked for their account link, received it and clicked it got a
    # 500. The link IS the way in, so there was nothing to work around, and it
    # sat there because no test opened the page.
    #
    # url_for and render_template are checked above by name; this is the same
    # idea one level down — the names the handler itself reads.
    module_scope = set(dir(builtins))
    app_tree = ast.parse(app_src)
    for node in app_tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        module_scope.add(sub.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            module_scope.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_scope.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                module_scope.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            # Names bound inside a module-level if/try are still module scope.
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    module_scope.add(sub.id)
                elif isinstance(sub, (ast.FunctionDef, ast.ClassDef)):
                    module_scope.add(sub.name)

    unresolved = []
    for fn in [n for n in ast.walk(app_tree) if isinstance(n, ast.FunctionDef)]:
        if not any("app.route" in ast.unparse(d) for d in fn.decorator_list):
            continue
        bound = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            elif isinstance(node, ast.arg):
                bound.add(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.Global):
                bound.update(node.names)
        for node in ast.walk(fn):
            if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                    and node.id not in bound and node.id not in module_scope):
                unresolved.append(f"app.py:{node.lineno} {fn.name}() reads {node.id}")
    s.check("every route reads only names that exist", not unresolved,
            detail=" | ".join(sorted(set(unresolved))[:4]))

    s.section("No two routes claim the same address")
    # The fourth fault, and nothing above catches it. Registering two handlers
    # on one path is legal, silent, and Werkzeug serves whichever rule it
    # matches first — so the other is dead code that still builds a perfectly
    # good URL. Everything else in this file passed while that was true:
    # stripe_success and booking_stripe_success both claimed
    # /book/stripe-success, the endpoint existed, the parameters were right,
    # and url_for produced the path. A guest settling a balance was handed to
    # the new-booking handler, which had no room_id to work from, and got a 500
    # on the page you see in the second after paying — with the money
    # unrecorded.
    #
    # Only visible from the url_map, which is why it is checked there rather
    # than from the source.
    by_address = {}
    for rule in m.app.url_map.iter_rules():
        verbs = tuple(sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}))
        by_address.setdefault((str(rule.rule), verbs), []).append(rule.endpoint)
    shared = {addr: eps for addr, eps in by_address.items() if len(eps) > 1}
    s.check("every path and method pair belongs to one endpoint", not shared,
            detail="; ".join(f"{addr[0]} {list(addr[1])} -> {eps}"
                             for addr, eps in sorted(shared.items())[:3]))
    # ...and the check can see one, so an empty result means there are none
    # rather than that the loop found nothing to look at.
    s.check("it is reading a real url_map", len(by_address) > 200,
            detail=f"{len(by_address)} distinct address(es)")

    return s
