"""A template asking a row for a column it has not got.

guest_statement.html had `{% if company and company['siret'] %}` in it. There
is no `siret` column — it is `registration_number` — and Jinja does not raise
on a missing key: sqlite3.Row throws IndexError, Jinja catches it and hands
back Undefined, Undefined is falsy, and the `{% if %}` is simply never true.

So the SIRET line could never render. Not "was blank until somebody filled the
field in" — never, at all, however carefully the field was filled in. Every
business guest's statement went out without the one identifier a French note
has to carry, the page rendered perfectly, and nothing anywhere reported it.

  THAT IS THE WHOLE FAULT CLASS. A misspelt column in Python is an IndexError
  on the first request. The same misspelling in a template is silence, and
  silence in a template that draws a legal document is the worst place in this
  app to put it.

  CHECKED AGAINST THE DATABASE, not against a list. A list of allowed keys is
  a second copy of the schema and goes stale the first time somebody adds a
  column, which is the same class of fault one level up.

  AND THE DETECTOR IS SHOWN TO WORK. Counting the references it examined is
  not the same as being able to find anything — the comparison could be
  neutered and the count would go on rising. So it is also pointed at the
  exact line that was wrong here, and has to catch it.

  NARROW ON PURPOSE. Only context variables whose table is KNOWN are checked.
  Guessing which table a variable came from is how a check like this starts
  crying wolf and gets deleted. Add a name to ROW_SOURCES when you pass a row
  from a table to a template, and this will hold it.
"""
import os
import re

from _harness import Suite, db
import _harness

m = _harness.m
TEMPLATES = os.path.join(_harness.ROOT, "templates")

# Context variable -> the table its row comes from. Only names that are always
# a row from that one table; anything ambiguous is left off rather than
# guessed at.
ROW_SOURCES = {
    "company": "company_info",
}


def _columns(conn, table):
    return {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}


def _strip_comments(body):
    # Comments are where the history gets written down, and the history has to
    # be able to name the column that was wrong.
    body = re.sub(r"\{#.*?#\}", " ", body, flags=re.S)
    return re.sub(r"<!--.*?-->", " ", body, flags=re.S)


def _misspelt(body, name, cols):
    """Every column this body asks `name` for that `cols` has not got.

    Pulled out so the sweep can be pointed at a string that is KNOWN to be
    wrong and shown to catch it.
    """
    pattern = re.compile(
        r"\b" + re.escape(name)
        + r"(?:\['([a-z_]+)'\]|\[\"([a-z_]+)\"\]|\.([a-z_]+))")
    out = []
    for found in pattern.finditer(_strip_comments(body)):
        key = found.group(1) or found.group(2) or found.group(3)
        # `items`, `keys` and the rest are Row methods, not columns.
        if key in cols or hasattr({}, key):
            continue
        out.append(key)
    return out


def run():
    s = Suite("Columns a template asks for")
    conn = db()
    try:
        s.section("Every column a template names is a column that exists")
        wrong, looked_at = [], 0
        for name, table in sorted(ROW_SOURCES.items()):
            cols = _columns(conn, table)
            s.check("%s is a real table with columns" % table, bool(cols),
                    detail="the check reads the database itself, so an empty "
                           "answer here would make it vacuous")
            for filename in sorted(os.listdir(TEMPLATES)):
                if not filename.endswith(".html"):
                    continue
                body = open(os.path.join(TEMPLATES, filename),
                            encoding="utf-8").read()
                looked_at += len(re.findall(
                    r"\b" + re.escape(name) + r"(?:\[|\.)",
                    _strip_comments(body)))
                for key in _misspelt(body, name, cols):
                    wrong.append("%s: %s['%s']" % (filename, name, key))
        s.check("not one of them is misspelt", not wrong,
                detail="Jinja does not raise on a missing key — it hands back "
                       "Undefined, which is falsy, so the line simply never "
                       "renders: " + str(wrong))
        s.check("and it actually looked at some",
                looked_at >= 4, detail="%d reference(s) examined" % looked_at)

        s.section("And the detector is shown to work")
        # Counting what it examined is not the same as being able to detect
        # anything. This is the exact line that was wrong.
        real = _columns(conn, "company_info")
        s.check("it catches the one that was wrong",
                _misspelt("{% if company['siret'] %}x{% endif %}",
                          "company", real) == ["siret"],
                detail=str(_misspelt("{% if company['siret'] %}x{% endif %}",
                                     "company", real)))
        s.check("without crying wolf over a real column",
                _misspelt("{{ company['registration_number'] }}",
                          "company", real) == [],
                detail="a check that flags a correct line gets deleted")
        s.check("or over a method every row has",
                _misspelt("{% for k in company.keys() %}{% endfor %}",
                          "company", real) == [])
        s.check("and it ignores what a comment quotes",
                _misspelt("{# it used to say company['siret'] here #}",
                          "company", real) == [],
                detail="the history has to be allowed to name the column it "
                       "is about")

        s.section("The one it was written for")
        statement = open(os.path.join(TEMPLATES, "guest_statement.html"),
                         encoding="utf-8").read()
        s.check("the statement asks for registration_number",
                "company['registration_number']" in statement)
        s.check("and no longer for a column that does not exist",
                "company['siret']" not in _strip_comments(statement),
                detail="there is no siret column; it is registration_number")

        s.section("And both documents say when the numbers are missing")
        # A statement is what a business guest forwards to whoever pays them,
        # and it prints a VAT breakdown. A tax figure with no TVA number
        # beside it and nothing saying why is the wrong half to leave out.
        receipt = open(os.path.join(TEMPLATES, "pos_receipt.html"),
                       encoding="utf-8").read()
        s.check("the till receipt says so on the receipt",
                "manquants" in receipt,
                detail="it already did, which is how the asymmetry showed up")
        s.check("and the statement says so on the statement",
                "SIRET and TVA number missing" in statement,
                detail="it printed the VAT and omitted the number in silence")
        s.check("and readiness names which ones are missing",
                "What a receipt has to carry" in open(
                    os.path.join(_harness.ROOT, "app.py"),
                    encoding="utf-8").read(),
                detail="'company info incomplete' is not something anybody "
                       "can act on")
    finally:
        conn.close()
    return s
