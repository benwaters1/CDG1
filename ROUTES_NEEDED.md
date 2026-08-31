# Three routes to add

The three event pages are templates only — they need routes. Each is a plain
render with no data, so they can sit next to `events_info`:

    @app.route("/events/weddings")
    def events_weddings():
        return render_template("events_weddings.html")

    @app.route("/events/private")
    def events_private():
        return render_template("events_private.html")

    @app.route("/events/photoshoots")
    def events_photoshoots():
        return render_template("events_photoshoots.html")

## Until then, nothing breaks

The header submenu guards each link:

    {% if 'events_weddings' in url_map %}{{ url_for('events_weddings') }}
    {% else %}{{ url_for('events_info') }}#weddings{% endif %}

Without the routes the links fall back to the anchors on the events page. A
missing endpoint therefore cannot raise a BuildError and take down the header
on every page.

To switch them on, pass the endpoint names into the template context — for
example in a context processor:

    @app.context_processor
    def inject_url_map():
        return {"url_map": {r.endpoint for r in app.url_map.iter_rules()}}

## Sitemap and canonical

Add the three URLs to the sitemap. Each page carries its own title and meta
description, so they will not compete with events_info for the same terms:

  /events/weddings      "Weddings"
  /events/private       "Private Events"
  /events/photoshoots   "Photoshoots & Film"

---

# Data change, not code

**Workshop capacity is 8 in the database; it should be 15.**
Nothing in the templates hardcodes it — every figure on the site reads from
the session record. Change `capacity` on the workshop sessions and the
"places left" flags, the atelier totals and the registration cap all follow.

# New form fields

`book_room.html` now posts **`adults`** and **`children`** alongside the
existing `party_size` (which is still sent, kept in step by script, so the
route needs no change to keep working). To store them, read the two new
fields; to ignore them, do nothing and `party_size` behaves exactly as before.

# Waiting list

The no-availability state now posts to `subscribe` with
`source=waitlist`, `wanted_arrival` and `wanted_departure`. If the route
ignores unknown fields this already works as a plain signup; storing the two
dates is what makes it a waiting list.

# Returning guest

`book_rooms.html` shows a recognition line only when the view passes
`returning_guest` as `{name, last_room, last_year}`. Pass nothing and the
block does not render.

---

# One more route: the press page

    @app.route("/press")
    def press():
        return render_template("press.html")

Everything on it already appears on the restoration page. Giving it an address
makes it linkable and citable — it is the highest-authority proof on the site
and currently exists only as passing mentions.

The footer link is guarded the same way as the event pages, so it falls back
to the restoration page until the route exists.

Add /press to the sitemap.
