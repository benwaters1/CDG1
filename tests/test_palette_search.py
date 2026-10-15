"""The search box at the top of every staff page: "Search or jump to…".

It is the way round menus that do not yet list everything, so a page it cannot
find is a page nobody can find. Two faults, found by the owner on 15 October
2026 the day before launch:

  THE PHRASE, NOT THE WORDS. What was typed had to appear whole, so "photo
  mirror" and "add employee" found nothing: no entry holds those words side
  by side. People type two words far more often than a label.

  A NAME NOBODY USES. "mirror" did find the photo mirror, listed as
  "Photographs we do not own", which nobody looking for the photo mirror
  read as the answer.
"""
from _harness import Suite, clients

import _harness

m = _harness.m


def _urls(client, q):
    res = client.get("/api/palette", query_string={"q": q}).get_json()["results"]
    return {x["url"] for x in res}, [x["label"] for x in res]


def run():
    s = Suite("Search or jump to")
    oc, ec, _owner, _emp = clients()
    with m.app.test_request_context("/"):
        mirror = m.url_for("admin_photo_mirror")
        add_person = m.url_for("new_employee")

    s.section("Every word typed, not the phrase")
    for q in ("mirror", "photo mirror", "mirror photo", "Photo Mirror"):
        urls, labels = _urls(oc, q)
        s.check(f'"{q}" finds the photo mirror', mirror in urls, detail=str(labels))
    urls, labels = _urls(oc, "mirror zebra")
    s.check("but every word has to be there, not any one of them",
            mirror not in urls,
            detail=f"{labels} — matching any word would bury the answer under "
                   "everything that shares one")

    s.section("Under a name somebody would recognise")
    _urls_, labels = _urls(oc, "mirror")
    s.check("it is listed as the photo mirror",
            any(l.lower().startswith("photo mirror") for l in labels),
            detail=str(labels))

    s.section("Adding somebody to the team")
    for q in ("add employee", "team member", "new staff", "invite"):
        urls, labels = _urls(oc, q)
        s.check(f'"{q}" finds Add a team member', add_person in urls,
                detail=str(labels))
    urls, labels = _urls(ec, "add employee")
    s.check("and a member of staff, who cannot open it, is not offered it",
            add_person not in urls,
            detail=f"{labels} — offering a page that refuses them is worse "
                   "than not offering it")

    s.section("Every page it offers is a page")
    dead = []
    with m.app.test_request_context("/"):
        for label, endpoint, _kw in m.PALETTE_PAGES:
            try:
                m.url_for(endpoint)
            except Exception:
                dead.append(f"{label} ({endpoint})")
    s.check("each entry names a page that exists", not dead,
            detail=f"{dead} — a renamed page drops out of the search silently, "
                   "because the lookup is wrapped to keep the box working")
    return s


if __name__ == "__main__":
    print(run().report())
