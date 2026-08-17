"""What the public promo field tells a stranger about which codes exist.

/api/validate-promo-code is public, unauthenticated and cheap to script
against. It used to answer with seven different sentences — "isn't
recognized", "has expired", "already been fully redeemed" — which made it an
oracle: submit a guess, and the wording told you whether you had found a real
code. Codes get emailed out in marketing blasts, so they are not secrets, but
a field that confirms guesses is still one worth closing.

The check is that a real-but-unusable code and pure noise come back
indistinguishable. The deliberate exception is a minimum spend, which stays
specific because it is the only refusal a guest can act on.

The owner has to keep the detail somewhere, or "my code doesn't work" is
unanswerable, so this also checks the admin page still says why.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, ensure_room
import _harness

m = _harness.m
TAG = "ZZPROMO"


def _mk(conn, code, **over):
    fields = {
        "code": code, "description": "test", "discount_type": "percent",
        "discount_value": 10.0, "applies_to": "all", "active": 1,
        "valid_from": None, "valid_until": None, "max_redemptions": None,
        "redemption_count": 0, "min_spend": None, "max_discount_amount": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    fields.update(over)
    cols = ", ".join(fields)
    conn.execute(f"INSERT INTO promo_codes ({cols}) VALUES ({', '.join('?' * len(fields))})",
                 list(fields.values()))
    conn.commit()


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action = 'validate_promo_code'")
    conn.commit()
    conn.close()


def run():
    s = Suite("Promo privacy")
    _cleanup()
    room = ensure_room()
    pub = m.app.test_client()
    oc, ec, owner, emp = clients()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=2)).isoformat()

    conn = db()
    _mk(conn, f"{TAG}EXPIRED", valid_until=yesterday)
    _mk(conn, f"{TAG}OFF", active=0)
    _mk(conn, f"{TAG}USEDUP", max_redemptions=1, redemption_count=1)
    _mk(conn, f"{TAG}WRONGKIND", applies_to="restaurant")
    _mk(conn, f"{TAG}MINSPEND", min_spend=999999.0)
    conn.close()

    arrival = (datetime.now(timezone.utc).date() + timedelta(days=540)).isoformat()
    departure = (datetime.now(timezone.utc).date() + timedelta(days=542)).isoformat()

    def ask(code):
        r = pub.post("/api/validate-promo-code", data={
            "code": code, "category": "room", "room_id": str(room["id"]),
            "arrival_date": arrival, "departure_date": departure,
        })
        return (r.get_json() or {}).get("message", "")

    s.section("A real unusable code is indistinguishable from noise")
    noise = ask(f"{TAG}NOSUCHCODEATALL")
    for name in ("EXPIRED", "OFF", "USEDUP", "WRONGKIND"):
        s.check(f"{name.lower()} reads the same as an unknown code",
                ask(f"{TAG}{name}") == noise,
                detail=f"{ask(f'{TAG}{name}')!r} vs {noise!r}")

    s.check("and that message does not name a reason",
            all(w not in noise.lower() for w in ("expired", "redeemed", "active", "kind")),
            detail=noise)

    s.section("A minimum spend still says so — a guest can act on that one")
    minspend = ask(f"{TAG}MINSPEND")
    s.check("the minimum is stated", "minimum" in minspend.lower(), detail=minspend)
    s.check("and it is not the generic refusal", minspend != noise, detail=minspend)

    s.section("The owner can still see why")
    page = oc.get("/admin/promo-codes")
    html = page.get_data(as_text=True)
    s.check("the admin page loads", page.status_code == 200, page)
    s.check("it names the expiry", f"Expired on {yesterday}" in html)
    s.check("it says a code is switched off", "switched off" in html)
    s.check("it says a code is fully redeemed", "Fully redeemed" in html)
    s.check("a usable code is not flagged as a problem",
            html.count("Guests can't use this right now") >= 3)

    s.section("The rate limit is still there")
    conn = db()
    conn.execute("DELETE FROM submission_log WHERE action = 'validate_promo_code'")
    conn.commit()
    conn.close()
    codes = [ask(f"{TAG}GUESS{i}") for i in range(34)]
    s.check("guessing in bulk is throttled",
            any("too many" in (c or "").lower() for c in codes),
            detail=f"last said {codes[-1]!r}")

    _cleanup()
    return s
