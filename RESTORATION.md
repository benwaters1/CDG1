# The reveal slider — what it needs from you

`_interactive.html` has `the_reveal(pairs)`, built and tested. **It renders
nothing at all until you supply real image pairs** — no placeholder, no stock
photograph, no invented pairing.

## What to pass

```python
reveal_pairs = [
    {
        "before":     url_for('static', filename='reveal/emeraude-2014.jpg'),
        "after":      url_for('static', filename='reveal/emeraude-now.jpg'),
        "alt_before": "The room in 2014, plaster down, floor open to the joists",
        "alt_after":  "The same room today, made up, walls left as found",
        "caption":    "Chambre Émeraude, 2014 and now",
    },
]
```

## What makes a pair work

**Shot from the same position.** The whole effect depends on it — a foot to
the left and the two images fight each other instead of resolving. If the
before photographs already exist and were taken casually, pick the pairs where
the camera happened to be in roughly the same place and accept fewer of them.

**Same aspect ratio and same size.** The stage is 3:2. Mismatched ratios will
crop unpredictably on one side of the handle.

**Three pairs is plenty.** This is the most compelling thing that can be on a
restoration page, and it stops being compelling at ten.

## And this is where the drone earns itself

For the roof, the terraces and the scaffolding, a saved waypoint route flown
every month gives you pairs shot from an **identical position in the sky** —
which is the one thing a handheld camera cannot promise. Fly it now, fly it in
spring, and the pair is exact.
