# Error pages — two lines to register

`templates/error.html` is in this drop. It has no route yet, so a 404 still
returns the bare Flask page. Add near the bottom of `app.py`:

```python
@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404), 404


@app.errorhandler(500)
def server_error(e):
    # The 500 handler must not itself depend on anything that might be broken,
    # so it passes nothing but the code.
    return render_template("error.html", code=500), 500
```

The template extends `public_base.html`, so a guest who mistypes an address —
or follows a manage link that has expired — lands on the château rather than
on a stack trace, with routes back to the house, the rooms, find-my-booking
and contact.

One caveat worth knowing: if the 500 is caused by something in `public_base`
itself, rendering the error page will fail too and Flask falls back to its own.
That is the correct trade — a themed error page is worth having for the 99% of
failures that are route-level.
