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
