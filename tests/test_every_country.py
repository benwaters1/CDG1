"""Every country can book, and the country pickers can be searched.

18 October 2026: a guest from Ukraine could not book a room. The telephone
code menu -- which the country of residence list is drawn from -- held 48
countries, Ukraine not among them, and the number is required, so there was no
way through. Most of the world was missing. And the menu was in no order a
guest could use (owner: "it should have a search thing where u put your
country code or type the name and it selects it").

These templates arrive from the design side as whole files, so this holds the
list and the search on every public form that asks, and goes red the day a
handover brings the short list back.
"""
import re

from _harness import Suite, db
import _harness

m = _harness.m
MUST = {"UA": "+380", "PL": "+48", "RO": "+40", "TR": "+90", "NG": "+234", "MA": "+212",
        "RU": "+7", "VN": "+84", "PE": "+51", "BG": "+359"}


def _selects(page):
    phone = re.search(r'<select[^>]*data-phone-cc[^>]*>(.*?)</select>', page, re.S)
    country = re.search(r'<select[^>]*data-country[^>]*>(.*?)</select>', page, re.S)
    return phone, country


def run():
    s = Suite("Every country can book, and the pickers search")
    conn = db()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    # a sitting of a workshop still on sale: one switched off 404s (27 Oct)
    sess = conn.execute("SELECT workshop_sessions.id FROM workshop_sessions "
                        "JOIN workshops ON workshops.id = workshop_sessions.workshop_id "
                        "WHERE workshops.active = 1 AND start_date >= ? AND cancelled_at IS NULL "
                        "ORDER BY start_date LIMIT 1", (m.house_today().isoformat(),)).fetchone()
    conn.close()
    guest = m.app.test_client()
    pages = {"the room booking form": f"/book/{room['id']}"}
    if sess:
        pages["the workshop registration"] = f"/workshops/register/{sess['id']}"
    for where, url in pages.items():
        s.section(where)
        page = guest.get(url).get_data(as_text=True)
        phone, country = _selects(page)
        s.check("it has a telephone code menu", phone is not None)
        if not phone:
            continue
        codes = re.findall(r'<option value="(\+\d+)" data-iso="([A-Z]{2})"', phone.group(1))
        isos = [iso for _c, iso in codes]
        s.check("every country with a dialling code is in it, not 48",
                len(codes) >= 230, detail=f"{len(codes)}")
        missing = [f"{iso} {code}" for iso, code in MUST.items() if (code, iso) not in codes]
        s.check("Ukraine among them, and the others that were missing", not missing, detail=str(missing))
        s.check("each country once", len(isos) == len(set(isos)))
        names = [re.sub(r"\s*\+\d+$", "", n).strip() for n in
                 re.findall(r'<option value="\+\d+"[^>]*>([^<]*)</option>', phone.group(1))]
        s.check("in alphabetical order", names == sorted(names, key=lambda n: n.casefold()),
                detail=str(names[:6]))
        s.check("and it can be searched by typing", "data-searchable" in page[phone.start():phone.end()]
                and "select[data-searchable]" in page)
        if country:
            ccount = re.findall(r'<option value="([^"]+)" data-iso="([A-Z]{2})"', country.group(1))
            s.check("the country of residence lists the same countries",
                    len(ccount) >= 230 and ("Ukraine", "UA") in ccount, detail=f"{len(ccount)}")
            s.check("and can be searched too", "data-searchable" in page[country.start():country.end()])
    return s


if __name__ == "__main__":
    print(run().report())
