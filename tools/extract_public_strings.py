"""Every sentence a guest reads, as the keys the page translator looks up.

    python tools/extract_public_strings.py            # report
    python tools/extract_public_strings.py --write    # write the to-do list

WHY IT ASKS THE RENDERED PAGES RATHER THAN THE TEMPLATES.

A source sweep over templates/ finds the prose but not the prose's KEY. The
page translator matches on the text as it reaches the browser: after Jinja has
run, after the includes have been pulled in, whitespace-normalised. A key
lifted from a template carries the template's indentation and its `{{ }}`
holes, and matches nothing. So this drives the app, collects the text nodes the
translator itself would collect — the same page_text_spans, not a second
implementation of it — and reports what has no translation yet.

It also means a page nobody remembered is in the list: the routes come from
the url_map, so a new public page appears here the first time it is served.

WHAT IT DELIBERATELY LEAVES OUT.

Anything that is not prose. A price, a date, a reference code, a room name
typed by the owner — those come out of the database and change per request, so
a translation keyed on today's value would be dead tomorrow and a hit would be
worse than a miss. The filter is "three or more runs of letters", which keeps
sentences and drops "GD-1042", "€220" and "14:30".
"""
import argparse
import collections
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Never against the real database: this drives the app, and app.py creates and
# seeds whatever GUDANES_DB_PATH points at.
#
# A FRESH PATH EVERY RUN, not a fixed one. app.py decides whether to bootstrap
# with `fresh = not os.path.exists(DB_PATH)`, so a fixed name that a killed run
# left behind empty is a schema-less file the app then declines to seed — and
# every page 500s on "no such table: app_settings", which says nothing about
# why.
import atexit
import tempfile
_fd, _db = tempfile.mkstemp(prefix="gudanes_strings_", suffix=".db")
os.close(_fd)
os.remove(_db)
os.environ["GUDANES_DB_PATH"] = _db


@atexit.register
def _drop_scratch_db():
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            os.remove(_db + suffix)
        except OSError:
            pass

import app as m           # noqa: E402
import translations       # noqa: E402

# Importing app.py does NOT create the schema — init_db() only runs under
# __main__ (app.py:72528), so every page 500s on "no such table" without this.
# tests/_harness.py calls it for the same reason.
m.init_db()

WORDS = re.compile(r"[A-Za-z][A-Za-z'’-]*")


# Things made of words that are nonetheless not prose, and would be damage
# rather than translation. An address and an email are the same in every
# language; a translated one is a guest writing to nobody.
NOT_PROSE = re.compile(
    r"""^(?:
          \S+@\S+\.\S+                      # an email address
        | (?:https?://|www\.)\S+            # a link
        | [+()\d][\d\s()+.-]{6,}            # a telephone number
        )$""", re.X)


def is_prose(key):
    """Worth translating: two or more words, and not an address or a code."""
    if len(key) < 3 or NOT_PROSE.match(key):
        return False
    return len(WORDS.findall(key)) >= 2


def public_paths():
    """Every public page that takes no parameters, from the app's own map."""
    out = []
    for rule in m.app.url_map.iter_rules():
        if rule.arguments or "GET" not in (rule.methods or ()):
            continue
        ep = rule.endpoint
        if ep.startswith("static") or "webhook" in ep:
            continue
        out.append(str(rule))
    return sorted(set(out))


def collect():
    """{key: [pages it appears on]} for every untranslated guest sentence."""
    client = m.app.test_client()
    seen = collections.OrderedDict()
    pages = 0
    for path in public_paths():
        try:
            r = client.get(path)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        if not (r.content_type or "").startswith("text/html"):
            continue
        body = r.get_data(as_text=True)
        if '<body class="g' not in body:      # the staff app is not in scope
            continue
        pages += 1
        for _s, _e, text in m.page_text_spans(body):
            key = m.translation_key(text)
            if not is_prose(key):
                continue
            seen.setdefault(key, []).append(path)
    return seen, pages


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="write pending-design/to-translate.txt")
    args = ap.parse_args(argv[1:])

    seen, pages = collect()
    rows = []
    for lang in ("fr", "es"):
        table = translations.TABLES.get(lang, {})
        missing = [k for k in seen if k not in table or table[k] == k]
        rows.append((lang, len(seen) - len(missing), len(seen), missing))

    print("public pages walked        %d" % pages)
    print("sentences a guest reads    %d" % len(seen))
    for lang, done, total, missing in rows:
        pct = 100 * done // max(total, 1)
        print("  %s  %5d / %-5d translated  (%d%%)  -- %d to go"
              % (lang, done, total, pct, len(missing)))

    if args.write:
        out = os.path.join(ROOT, "pending-design", "to-translate.txt")
        with open(out, "w", encoding="utf-8") as fh:
            for lang, _d, _t, missing in rows:
                fh.write("=== %s: %d missing ===\n" % (lang, len(missing)))
                for k in missing:
                    fh.write("%s\n" % k)
                fh.write("\n")
        print("\nwrote %s" % os.path.relpath(out, ROOT))
    else:
        worst = sorted(seen.items(), key=lambda kv: -len(kv[1]))[:3]
        print("\non the most pages:")
        for k, where in worst:
            print("   x%-3d %s" % (len(where), k[:70]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
