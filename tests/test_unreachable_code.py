"""Functions defined and called by nothing.

cancel_booking_extra was one of these. It had existed since the extras were
built, carefully written to correct the stock ledger by appending rather than
rewriting — and no route ever called it, so a guest could add a case of wine
to their stay and nobody could take it off. It looked finished from outside,
which is what makes this class expensive: the thing you would notice is an
absence.

THE SWEEP HAS TO KNOW HOW A FUNCTION CAN BE REACHED, and there are more ways
than "somebody calls it by name":

  - a route, reached by URL;
  - a decorator, applied rather than called;
  - a Flask hook — before_request, after_request, errorhandler, context
    processor, template filter;
  - HANDED SOMEWHERE rather than called: every scheduled job in this app is
    an element of a tuple in AUTOMATION_JOBS, and nothing calls
    run_backup_email_job by name;
  - a JINJA GLOBAL, registered under a DIFFERENT NAME and used only in
    templates. format_date_range is exactly this: registered as `date_range`,
    used by three templates, and invisible to a sweep looking for its own
    name. A sweep that did not know this would have had it deleted.

So the exceptions are not a list of names to forgive. They are the ways a
function is genuinely reachable, worked out from the source — which is the
only version that stays true when somebody adds a hook next year.
"""
import ast
import os
import re

from _harness import Suite
import _harness

ROOT = _harness.ROOT
TEMPLATES = os.path.join(ROOT, "templates")


def _template_text():
    out = []
    for name in os.listdir(TEMPLATES):
        if name.endswith((".html", ".xml", ".js", ".txt")):
            with open(os.path.join(TEMPLATES, name), encoding="utf-8") as fh:
                out.append(fh.read())
    return "\n".join(out)


def _reachable(src, tree, templates):
    """Every name that something can actually get to, and how."""
    called, decorated, hooked, exported = set(), set(), set(), {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
        # A function HANDED somewhere is reachable too, and this is the way
        # every scheduled job in this app is wired: AUTOMATION_JOBS is a list
        # of tuples whose last element is the function itself. Nothing calls
        # run_backup_email_job by name and it runs every night.
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            called.add(node.id)
        # Registered under another name: app.jinja_env.globals["x"] = fn,
        # filters, and anything assigned into a dict or passed as a value.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(node.value, ast.Name)):
                    key = getattr(target.slice, "value", None)
                    if isinstance(key, str):
                        exported[node.value.id] = key
        if isinstance(node, ast.FunctionDef):
            for d in node.decorator_list:
                seg = ast.get_source_segment(src, d) or ""
                if any(h in seg for h in ("app.route", "before_request",
                                          "after_request", "errorhandler",
                                          "context_processor", "template_filter",
                                          "template_global", "teardown")):
                    hooked.add(node.name)
                # A decorator is applied, not called by name.
                if isinstance(d, ast.Name):
                    decorated.add(d.id)
                elif isinstance(d, ast.Call) and isinstance(d.func, ast.Name):
                    decorated.add(d.func.id)
    return called, decorated, hooked, exported


def run():
    s = Suite("Nothing can reach it")

    with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    templates = _template_text()
    called, decorated, hooked, exported = _reachable(src, tree, templates)

    # MODULE LEVEL ONLY. A nested function is returned, closed over, or
    # handed to something -- a decorator's `wrapped` is never called by name
    # and is not dead. Only a top-level def that nothing reaches is a helper
    # sitting there looking finished.
    defined = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            defined.setdefault(node.name, node.lineno)

    unreachable = []
    for name, line in sorted(defined.items(), key=lambda kv: kv[1]):
        if name.startswith("_") or name in called or name in decorated or name in hooked:
            continue
        # Registered under another name, and used in a template by THAT name.
        alias = exported.get(name)
        if alias and re.search(rf"\b{re.escape(alias)}\s*\(", templates):
            continue
        if re.search(rf"\b{re.escape(name)}\s*\(", templates):
            continue
        unreachable.append(f"{name}() at app.py:{line}")

    s.section("Every function can be got to somehow")
    s.check(f"all {len(defined)} functions are reachable", not unreachable,
            detail=("nothing calls: " + "; ".join(unreachable[:4]))
                   if unreachable else "")

    s.section("The sweep knows the ways in")
    # Without these it either passes on everything or demands the deletion of
    # working code — and it nearly did the second, to format_date_range.
    s.check("a route counts as reachable", "dashboard" in hooked or "dashboard" in called,
            detail="a page is reached by URL, not by a call")
    s.check("a decorator counts", "owner_required" in decorated,
            detail="applied rather than called")
    s.check("something registered under another name counts",
            exported.get("format_date_range") == "date_range",
            detail="registered as date_range and used by three templates; a "
                   "sweep looking for its own name would have had it deleted")
    s.check("and that alias is genuinely used in a template",
            re.search(r"\bdate_range\s*\(", templates) is not None,
            detail="if it were not, the exception above would be forgiving "
                   "something that really is dead")

    s.section("It can still tell when something is dead")
    # The check above is worthless if it cannot fail. Two names that were
    # genuinely unreachable and have been removed.
    for gone in ("rota_conflict_summary", "beverage_pours_for"):
        s.check(f"{gone} is gone rather than sitting there unused",
                gone not in defined,
                detail="a helper called by nothing reads as finished from "
                       "outside and does nothing")

    return s


if __name__ == "__main__":
    print(run().report())
