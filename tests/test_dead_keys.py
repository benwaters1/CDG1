"""Keys built into a returned dict that nothing ever reads.

test_dead_context checks that every value PASSED to render_template reaches
its template. It cannot see INSIDE one. A function that returns a dict of
twelve keys arrives as a single kwarg, and the eleven nobody opens are
invisible to it — computed on every page load and thrown away.

That is not a theoretical shape. It is how net profit came to be built on a
labour cost that silently excluded everybody the app could not put a wage
on: labour_cost_breakdown worked out how many people were costed from a
typed wage, how many from a free-text pay note, and who could not be costed
at all — and the next function along dropped two of the three, and the
month summary dropped the third. Every one of those was a key nothing read.

LIKE THE UNREACHABLE-FUNCTION SWEEP, THIS HAS TO KNOW THE WAYS IN, and a
dict key has more of them than a function name:

  - a CONTEXT PROCESSOR's keys become bare Jinja globals. inject_user
    returns thirty-odd and no line anywhere writes ["user"]; templates say
    {{ user.name }} and {{ can('x') }}.
  - an EMAIL CONTEXT's keys are {placeholders} in templates that live in
    the DATABASE. CLAUDE.md says so in as many words: email templates are
    data, not code. Nothing in the repo mentions {manage_url} and it is
    substituted on every confirmation the house sends.
  - a dict that is SERIALISED WHOLE — dumped to JSON for a data request,
    or handed to the reply drafter as context — uses every key it has by
    definition.
  - and a template can read a key as a bare name: {{ prefill_email }}.

The first version of this sweep knew none of that and reported 88, of which
about seventy were its own ignorance. A sweep that cries wolf gets an
exception added for every finding until it means nothing, so the exclusions
below are the ways in, worked out from the source — not a list of names to
forgive.
"""
import ast
import os
import re
import sqlite3

from _harness import Suite
import _harness

ROOT = _harness.ROOT
TEMPLATES = os.path.join(ROOT, "templates")

# Functions whose whole return value is serialised or handed on entire, so
# every key in them is used by definition. Kept short and each one says why.
SERIALISED_WHOLE = {
    # Written out as the guest's own copy of everything held about them.
    "guest_data_export": "dumped to JSON for a data request",
    "guest_data_erase": "reported back to the requester whole",
    # Handed to the reply drafter as context about what the house sells.
    "current_offerings_snapshot": "passed whole as model context",
}

# Minimum keys before a returned dict is worth sweeping. Below this it is
# usually a two- or three-value result being unpacked, not a payload.
MIN_KEYS = 3


def _templates():
    out = []
    for name in os.listdir(TEMPLATES):
        if name.endswith((".html", ".xml", ".js", ".txt")):
            with open(os.path.join(TEMPLATES, name), encoding="utf-8") as fh:
                out.append(fh.read())
    return "\n".join(out)


def _database_text():
    """Email and notification bodies, which are DATA in this app.

    A merge tag like {manage_url} appears nowhere in the repo and is
    substituted into every confirmation the house sends.
    """
    chunks = []
    try:
        conn = sqlite3.connect(_harness.m.DB_PATH)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")
                    if str(r[2]).upper().startswith("TEXT")]
            if not cols:
                continue
            for row in conn.execute(f"SELECT {','.join(cols)} FROM {table}"):
                chunks.extend(str(v) for v in row if v)
        conn.close()
    except sqlite3.Error:
        # Reported by the check below rather than silently passing.
        return None
    return "\n".join(chunks)


def run():
    s = Suite("Computed and thrown away")

    with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    templates = _templates()
    db_text = _database_text()

    s.section("The sweep can see the ways in")
    s.check("the email templates were readable",
            db_text is not None,
            detail="without them every merge tag reads as dead, and the "
                   "sweep reports thirty things that are fine")
    db_text = db_text or ""

    processors = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for d in node.decorator_list:
                if "context_processor" in (ast.get_source_segment(src, d) or ""):
                    processors.add(node.name)
    s.check("a context processor is recognised", bool(processors),
            detail="its keys are bare Jinja globals; nothing writes "
                   '["user"] and every template says {{ user.name }}')

    dead = []
    swept = 0
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        if fn.name in processors or fn.name in SERIALISED_WHOLE:
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
                continue
            keys = [k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if len(keys) < MIN_KEYS:
                continue
            # A MERGE-TAG NAMESPACE is reachable as a whole. If any key of
            # this dict is live as a {placeholder} in the stored templates,
            # the owner writes against this dict by name -- so its siblings
            # are available fields, not dead code, and the next campaign
            # they type could use one tomorrow. Deleting one would break a
            # template nobody has written yet, silently, in a body of text
            # that lives in the database and shows up in no diff.
            #
            # Structural rather than a list of names, so it notices the next
            # such function without anybody maintaining it.
            if any(re.search(rf'\{{{re.escape(k)}\}}', db_text)
                   for k in keys):
                continue

            for key in keys:
                swept += 1
                k = re.escape(key)
                if re.search(rf'\["{k}"\]|\[\'{k}\'\]|\.get\(["\']{k}["\']', src):
                    continue
                if re.search(rf'\b{k}\b', templates):
                    continue
                if re.search(rf'\{{{k}\}}', db_text):
                    continue
                dead.append(f"{fn.name}() -> {key!r} (app.py:{node.lineno})")

    dead = sorted(set(dead))
    s.section("Every key a function builds is read by something")
    s.check(f"all {swept} key(s) across the returned payloads are read",
            not dead,
            detail=("nothing reads: " + "; ".join(dead[:5])
                    + (f"  (+{len(dead) - 5} more)" if len(dead) > 5 else ""))
                   if dead else "")

    s.section("It can still tell when something is dead")
    # Worthless if it cannot fail. These were real, and each was either
    # surfaced or removed rather than added to an exception list.
    s.check("the labour counts now reach a page",
            re.search(r'"labour_unpriced"', src) is not None
            and "labour_unpriced" in templates,
            detail="net profit was revenue minus a labour cost that could "
                   "not price anybody without a wage on file, and no page "
                   "said so")
    s.check("and the two kinds of unreported incident are told apart",
            'insurer["no_policy"]' in src or "insurer['no_policy']" in src,
            detail="'send it' and 'work out which policy this falls under' "
                   "are different jobs, and the page showed one total")

    s.section("A merge-tag namespace is reachable as a whole")
    # The rule that keeps this from deleting an available field. Worth a
    # check of its own, because it is the one exclusion that forgives a key
    # on the strength of its SIBLINGS rather than itself.
    # Which particular tag is live is a property of templates the owner
    # edits, so this checks the MECHANISM -- that stored templates carry
    # merge tags at all -- rather than naming one and going red the day
    # somebody rewrites a confirmation.
    live_tags = set(re.findall(r"\{([a-z_]{3,30})\}", db_text))
    s.check("stored templates carry merge tags", bool(live_tags),
            detail=f"{len(live_tags)} distinct tag(s), e.g. "
                   + ", ".join("{" + t + "}" for t in sorted(live_tags)[:4])
                   + " -- substituted into mail the house sends, and "
                     "appearing nowhere in the repo")
    s.check("so its siblings are treated as fields, not as dead code",
            not any("campaign_context_for" in d for d in dead),
            detail="deleting {full_name} would break a campaign nobody has "
                   "written yet, in a template that lives in the database "
                   "and shows up in no diff")

    s.section("The exclusions are reasons, not a list of names")
    for name, why in SERIALISED_WHOLE.items():
        s.check(f"{name} is excused because it is {why}",
                re.search(rf"\b{re.escape(name)}\b", src) is not None,
                detail="an exception for a function that no longer exists is "
                       "an exception nobody will ever re-examine")

    return s


if __name__ == "__main__":
    print(run().report())
