"""Staff chat.

The things worth asserting are the ones that are invisible when wrong: a DM
readable by someone outside it, an unread badge that counts your own messages,
or a channel that stays bold after you have read it. Each of those makes the
feature quietly untrustworthy rather than obviously broken.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZCHAT"


def run():
    s = Suite("Chat")
    oc, ec, owner, emp = clients()
    first_name = (emp["name"] or "").split(" ")[0]

    s.section("The fixed channels exist")
    conn = db()
    areas = {r["slug"]: r["name"] for r in conn.execute(
        "SELECT slug, name FROM chat_channels WHERE kind = 'area'").fetchall()}
    conn.close()
    for slug in ("everyone", "kitchen", "housekeeping", "maintenance"):
        s.check(f"#{slug} is seeded", slug in areas)

    s.section("Posting to an area channel")
    r = oc.post("/chat/kitchen/post",
                data={"body": f"{TAG} the walk-in needs defrosting @{first_name}"},
                follow_redirects=True)
    conn = db()
    msg = conn.execute(
        """SELECT m.*, u.name AS author FROM chat_messages m
           LEFT JOIN users u ON u.id = m.user_id
           WHERE m.body LIKE ? ORDER BY m.id DESC LIMIT 1""", (TAG + "%",)).fetchone()
    conn.close()
    s.check("the message is stored", msg is not None, r)
    if msg:
        s.check("with its author", msg["author"] == owner["name"],
                detail=f"got {msg['author']!r}")

    s.section("Unread counts")
    conn = db()
    before = m.chat_unread_total(conn, {"id": emp["id"]})
    own = m.chat_unread_total(conn, {"id": owner["id"]})
    conn.close()
    s.check("the message is unread for the other person", before >= 1,
            detail=f"got {before}")
    # Counting your own messages is the classic version of this bug: the badge
    # never clears and people stop trusting it.
    s.check("your own message is not unread to you", own == 0, detail=f"got {own}")

    ec.get("/chat/kitchen")
    conn = db()
    after = m.chat_unread_total(conn, {"id": emp["id"]})
    conn.close()
    s.check("opening the channel clears it", after == 0, detail=f"got {after}")

    s.section("@mentions notify")
    conn = db()
    mentioned = conn.execute(
        """SELECT COUNT(*) AS c FROM notifications
           WHERE kind = 'chat' AND user_id = ?""", (emp["id"],)).fetchone()["c"]
    conn.close()
    s.check("the person named with @ is notified", mentioned >= 1,
            detail=f"{mentioned} notifications")

    s.section("Direct messages are private")
    r = oc.get(f"/chat/with/{emp['id']}")
    slug = (r.headers.get("Location") or "").rsplit("/", 1)[-1]
    s.check("opening a DM redirects to a channel", r.status_code == 302 and slug.startswith("dm-"),
            detail=f"HTTP {r.status_code} -> {slug!r}")

    if slug.startswith("dm-"):
        # Same channel from either side, or each person sees half a conversation.
        r2 = ec.get(f"/chat/with/{owner['id']}")
        s.check("the same DM is found from the other side",
                (r2.headers.get("Location") or "").endswith(slug),
                detail=f"got {r2.headers.get('Location')}")

        oc.post(f"/chat/{slug}/post", data={"body": f"{TAG} quick word about Friday"},
                follow_redirects=True)
        conn = db()
        channel = conn.execute("SELECT * FROM chat_channels WHERE slug = ?", (slug,)).fetchone()
        # A stranger must not be able to read it, by id or by URL.
        leaked = m.can_read_channel(conn, channel, {"id": 987654})
        conn.close()
        s.check("someone outside the DM cannot read it", not leaked)

        conn = db()
        stranger_id = conn.execute(
            """INSERT INTO users (email, password_hash, role, name, status, created_at)
               VALUES (?, 'x', 'employee', ?, 'active', ?)""",
            (f"{TAG.lower()}stranger@example.invalid", f"{TAG} Stranger",
             _harness.datetime_now())).lastrowid
        conn.commit()
        conn.close()
        stranger = m.app.test_client()
        with stranger.session_transaction() as sess:
            sess["user_id"] = stranger_id
        rr = stranger.get(f"/chat/{slug}")
        s.check("and gets a 403 rather than the thread", rr.status_code == 403,
                detail=f"HTTP {rr.status_code}")

    s.section("Guards")
    s.check("an unknown channel is a clean 404", oc.get("/chat/no-such-channel").status_code == 404)
    r = oc.post("/chat/kitchen/post", data={"body": "   "}, follow_redirects=True)
    conn = db()
    blanks = conn.execute(
        "SELECT COUNT(*) AS c FROM chat_messages WHERE TRIM(body) = ''").fetchone()["c"]
    conn.close()
    s.check("an empty message is not stored", blanks == 0, r, detail=f"{blanks} blank rows")
    s.check("messaging yourself just returns to the list",
            oc.get(f"/chat/with/{owner['id']}").status_code == 302)

    conn = db()
    conn.execute("DELETE FROM chat_messages WHERE body LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM notifications WHERE kind = 'chat'")
    conn.execute("DELETE FROM chat_channels WHERE kind = 'dm'")
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()
    return s
