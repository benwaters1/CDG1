# robots.txt and sitemap.xml — two routes

Neither exists, so search engines are crawling the admin and guest-token URLs
alongside the public pages, and have no map of what is actually here.

```python
from flask import Response, url_for


@app.route("/robots.txt")
def robots():
    """Keep crawlers out of anything token-shaped or staff-only.

    The manage links are unguessable, but they do leak into referrer headers
    and analytics, and a crawler that finds one would index a guest's booking.
    """
    body = "\n".join([
        "User-agent: *",
        "Disallow: /admin",
        "Disallow: /staff",
        "Disallow: /pos",
        "Disallow: /book/manage/",
        "Disallow: /workshops/manage/",
        "Disallow: /restaurant/manage/",
        "Disallow: /events/manage/",
        "Disallow: /account/",
        "Disallow: /login",
        "",
        f"Sitemap: {url_for('sitemap', _external=True)}",
        "",
    ])
    return Response(body, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap():
    """Only the public pages. Rooms and workshops are included individually,
    because those are the pages people actually search for."""
    conn = get_db()
    pages = [
        ("dashboard", 1.0), ("book_rooms", 0.9), ("workshops_public", 0.9),
        ("restaurant_info", 0.8), ("events_info", 0.8), ("facilities_page", 0.7),
        ("restoration_page", 0.7), ("gallery_page", 0.6), ("contact_page", 0.6),
        ("whats_on", 0.5), ("terms_page", 0.3),
    ]
    urls = []
    for endpoint, priority in pages:
        try:
            urls.append((url_for(endpoint, _external=True), priority))
        except Exception:
            pass  # endpoint renamed or removed — skip rather than 500

    for r in conn.execute("SELECT id FROM rooms WHERE is_active = 1"):
        urls.append((url_for("book_room", room_id=r["id"], _external=True), 0.8))
    for w in conn.execute("SELECT id FROM workshops WHERE is_active = 1"):
        urls.append((url_for("workshop_detail", workshop_id=w["id"], _external=True), 0.8))
    conn.close()

    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, pri in urls:
        body.append(f"  <url><loc>{loc}</loc><priority>{pri}</priority></url>")
    body.append("</urlset>")
    return Response("\n".join(body), mimetype="application/xml")
```

Check the `is_active` column names against your schema before running — if
either table uses a different flag, the query will raise.
