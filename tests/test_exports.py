"""Every CSV export.

There are around twenty of these and none were covered. They are the least
glamorous thing in the app and the most likely to break unnoticed: an export is
a second, parallel rendering of a query, so a renamed column breaks the download
while the page it came from still looks perfect. Nobody finds out until the
accountant asks for the file.

Discovered from the URL map rather than listed, so a new export is covered the
day it is added instead of the day someone remembers to add it here.
"""
from _harness import Suite, clients, db, ensure_room
import _harness

m = _harness.m


def run():
    s = Suite("Exports")
    oc, ec, owner, emp = clients()
    ensure_room()

    exports = sorted(
        (str(rule), rule.endpoint) for rule in m.app.url_map.iter_rules()
        if "GET" in rule.methods and "<" not in str(rule)
        and (str(rule).endswith(".csv") or "export" in rule.endpoint))
    s.section(f"{len(exports)} exports, found from the URL map")
    s.check("there are exports to check", bool(exports),
            detail="none found, so this suite proves nothing")

    broken, empty, ok = [], [], []
    for path, endpoint in exports:
        try:
            r = oc.get(path)
        except Exception as e:
            broken.append(f"{path} raised {type(e).__name__}")
            continue
        if r.status_code >= 400:
            # A deliberate refusal is fine — the payroll export withholds a file
            # when a shift is impossible, which is the right call. A 500 is not.
            if r.status_code >= 500:
                broken.append(f"{path} -> {r.status_code}")
            continue
        body = r.get_data(as_text=True)
        if r.status_code == 200 and "," not in body and body.strip():
            empty.append(f"{path} returned no comma-separated content")
        else:
            ok.append(path)

    s.check("no export returns a server error", not broken, detail="; ".join(broken[:4]))
    s.check("every export that answers 200 looks like CSV", not empty,
            detail="; ".join(empty[:4]))
    print(f"       ({len(ok)} produced a file, {len(exports) - len(ok)} redirected or refused)")

    s.section("An export is not a way around permissions")
    # The property that matters is not "staff cannot export sensitive things" —
    # it is that an export grants exactly what its own page grants. Downloading
    # the trades phonebook is fine precisely because staff can already read it;
    # downloading payroll is not, because they cannot. So compare the two rather
    # than guessing which exports are sensitive.
    #
    # Owner-gated pages are located by their endpoint name: export_x_csv guards
    # the same data as the x page.
    # Pair each export with the page it exports, by name: export_room_issues_csv
    # exports room_issues. Then the rule is simply that the two must agree —
    # room issues are staff work and both are open; payroll is not and both are
    # shut. Area membership is the wrong yardstick, because a page can sit in an
    # admin group and still be @login_required.
    mismatched, compared = [], 0
    for path, endpoint in exports:
        base = endpoint
        if base.startswith("export_"):
            base = base[len("export_"):]
        if base.endswith("_csv"):
            base = base[:-len("_csv")]
        page = m.app.view_functions.get(base)
        if not page:
            continue
        page_path = None
        for rule in m.app.url_map.iter_rules(base):
            if "GET" in rule.methods and "<" not in str(rule):
                page_path = str(rule)
                break
        if not page_path:
            continue
        compared += 1
        page_open = ec.get(page_path).status_code == 200
        export_open = ec.get(path).status_code == 200
        # Only one direction is a hole. An export open while its page is shut
        # hands over data the person was refused. The reverse — /guests readable
        # but the whole guest list not downloadable — is deliberately tighter:
        # looking someone up is operational, walking out with every name, email,
        # phone number and dietary note is not.
        if export_open and not page_open:
            mismatched.append(f"{path} is open but {page_path} is not")
    s.check(f"no export is looser than its own page ({compared} compared)", not mismatched,
            detail="; ".join(mismatched[:4]))

    s.section("The sensitive ones specifically")
    for path in ("/admin/payroll/export.csv", "/directory/pay-history/export.csv",
                 "/management/financials/export.csv"):
        if any(path == p for p, _ in exports):
            code = ec.get(path).status_code
            s.check(f"an employee is refused {path}", code != 200, detail=f"HTTP {code}")

    conn = db()
    conn.execute("DELETE FROM submission_log")
    conn.commit()
    conn.close()
    return s
