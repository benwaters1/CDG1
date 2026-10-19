"""Draw the Contact page's route map into templates/_route_map.html.

    python tools/draw_route_map.py

The towns are placed from their real positions -- equirectangular, scaled for
the latitude, which over a hundred and fifty kilometres is as true as a paper
map -- and the roads are drawn through the towns they pass, simplified. Every
figure is rounded to one decimal, so no coordinate ever reaches the page:
test_where_the_gates_are reads every template for one, because the only pin
the house publishes is the one somebody set standing at the gates.

It rewrites the block between the two markers in the template and nothing
else, so the legend and the words around the drawing stay hand-written.
Deterministic -- the scatter of peaks is seeded -- so running it twice changes
nothing, and test_route_map checks the template still matches what this draws.
"""
import io
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "templates", "_route_map.html")
BEGIN = "{#- drawn by tools/draw_route_map.py: begin -#}"
END = "{#- drawn by tools/draw_route_map.py: end -#}"

# The frame: Toulouse-Blagnac at the top, Puigcerdà at the foot, Carcassonne
# at the right. Portrait, because the valley runs north to south.
W_LNG, E_LNG, N_LAT, S_LAT = 1.30, 2.42, 43.67, 42.36
COS = math.cos(math.radians(43.0))
K = 600 / ((E_LNG - W_LNG) * COS)
H = round((N_LAT - S_LAT) * K)

T = {
    "airport": (43.6293, 1.3638), "toulouse": (43.6045, 1.4440),
    "pamiers": (43.1167, 1.6108), "foix": (42.9653, 1.6075),
    "tarascon": (42.8456, 1.6050), "cabannes": (42.7856, 1.6856),
    "chateau": (42.7905, 1.6700), "ax": (42.7194, 1.8389),
    "mirepoix": (43.0878, 1.8744), "carcassonne": (43.2130, 2.3491),
    "andorra": (42.5063, 1.5218), "pasdelacase": (42.5428, 1.7333),
    "hospitalet": (42.5890, 1.8010), "puigcerda": (42.4317, 1.9283),
}
# The valley road, shared: north of the château from Pamiers, south of it from
# l'Hospitalet. The château end of each is the drawing's mark, not a pin.
TRUNK_N = [T["pamiers"], (43.045, 1.629), T["foix"], (42.905, 1.598), T["tarascon"],
           (42.815, 1.640), T["chateau"]]
TRUNK_S = [T["hospitalet"], (42.655, 1.837), T["ax"], (42.765, 1.757), T["cabannes"],
           T["chateau"]]
ROUTES = {
    "toulouse": [T["airport"], (43.585, 1.425), (43.545, 1.475), (43.48, 1.56),
                 (43.40, 1.64), (43.25, 1.645)] + TRUNK_N,
    "carcassonne": [T["carcassonne"], (43.195, 2.16), (43.187, 2.033), T["mirepoix"],
                    (43.105, 1.74)] + TRUNK_N,
    "andorra": [T["andorra"], (42.534, 1.580), (42.577, 1.667), (42.552, 1.715),
                T["pasdelacase"]] + TRUNK_S,
    "barcelona": [(S_LAT - 0.005, 1.905), T["puigcerda"], (42.462, 1.892), (42.515, 1.845),
                  (42.548, 1.833)] + TRUNK_S,
}
RAIL = [(43.611, 1.454), (43.52, 1.415), (43.35, 1.488), (43.235, 1.590), T["pamiers"],
        (43.045, 1.622), T["foix"], (42.905, 1.592), T["tarascon"], (42.812, 1.648),
        T["cabannes"]]
ARIEGE = [(42.585, 1.785), (42.655, 1.828), (42.72, 1.832), (42.765, 1.765), (42.783, 1.695),
          (42.81, 1.642), (42.845, 1.612), (42.90, 1.604), (42.965, 1.616), (43.04, 1.632),
          (43.115, 1.603), (43.235, 1.574), (43.31, 1.532), (43.35, 1.474), (43.43, 1.428),
          (43.515, 1.405)]
GARONNE = [(43.36, 1.30), (43.46, 1.33), (43.515, 1.405), (43.60, 1.437), (43.67, 1.395)]
ANDORRA = [(42.655, 1.43), (42.650, 1.52), (42.625, 1.62), (42.600, 1.72), (42.570, 1.786),
           (42.510, 1.740), (42.470, 1.680), (42.430, 1.580), (42.432, 1.480), (42.470, 1.430),
           (42.550, 1.410), (42.655, 1.43)]
BORDER_W = [(42.725, 1.30), (42.69, 1.36), (42.655, 1.43)]
BORDER_E = [(42.570, 1.786), (42.520, 1.845), (42.475, 1.875), (42.445, 1.915),
            (42.437, 1.950), (42.470, 2.030), (42.440, 2.110), (42.425, 2.260), (42.405, 2.42)]


def xy(lat, lng):
    return ((lng - W_LNG) * COS * K, (N_LAT - lat) * K)


def f(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


def path(points, smooth=True):
    pts = [xy(*p) for p in points]
    if not smooth or len(pts) < 3:
        return "M" + " L".join(f"{f(x)} {f(y)}" for x, y in pts)
    # Catmull-Rom to cubic Bezier, so a road bends rather than kinks.
    d = f"M{f(pts[0][0])} {f(pts[0][1])}"
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f" C{f(c1[0])} {f(c1[1])} {f(c2[0])} {f(c2[1])} {f(p2[0])} {f(p2[1])}"
    return d


def peaks():
    """A scatter of small peaks over the Pyrenees, thinning into the foothills,
    kept clear of every road, the railway, the river and every label, so the
    mountains never read as noise over the route."""
    lines = []
    for pts in list(ROUTES.values()) + [RAIL, ARIEGE]:
        p = [xy(*q) for q in pts]
        for a, b in zip(p, p[1:]):
            n = max(1, int(math.dist(a, b) / 6))
            lines += [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
                      for i in range(n + 1)]
    cx, cy = xy(*T["chateau"])
    # The towns, the title card, the compass and the château's name.
    clear = ([xy(*T[k]) for k in T] + [(470, 70), (528, 650)]
             + [(cx + 30 + i * 30, cy - 50) for i in range(9)]
             + [(cx + 30 + i * 30, cy - 26) for i in range(9)])
    rnd = random.Random(1741)
    out = []
    for gy in range(int(xy(42.98, 1.5)[1]), H - 20, 26):
        for gx in range(14, 590, 30):
            x, y = gx + rnd.uniform(-7, 7), gy + rnd.uniform(-6, 6)
            lat = N_LAT - y / K
            if lat > 42.86 and rnd.random() < (lat - 42.86) * 6:
                continue
            if min(math.dist((x, y), q) for q in lines) < 13:
                continue
            if min(math.dist((x, y), q) for q in clear) < 34:
                continue
            s = rnd.uniform(0.75, 1.25)
            out.append(f'<path d="M{f(x - 7 * s)} {f(y + 4 * s)} L{f(x)} {f(y - 5 * s)} '
                       f'L{f(x + 7 * s)} {f(y + 4 * s)}"/>')
    return out


def label(x, y, text, cls, anchor="start", extra=""):
    return (f'<text x="{f(x)}" y="{f(y)}" class="{cls}" text-anchor="{anchor}"{extra}>'
            f'{text}</text>')


def town(key, text, dx=8, dy=4, anchor="start", cls="g-rmap__town", r=3.2):
    x, y = xy(*T[key])
    return (f'<circle class="g-rmap__dot" cx="{f(x)}" cy="{f(y)}" r="{r}"/>'
            + label(x + dx, y + dy, text, cls, anchor))


def shield(lat, lng, text):
    x, y = xy(lat, lng)
    wdt = 10 + 7.2 * len(text)
    return (f'<g class="g-rmap__shield" transform="translate({f(x)} {f(y)})">'
            f'<rect x="{f(-wdt / 2)}" y="-9" width="{f(wdt)}" height="18" rx="3"/>'
            f'<text y="4.5" text-anchor="middle">{text}</text></g>')


def place_label(lat, lng, text, cls, rotate=None):
    x, y = xy(lat, lng)
    extra = f' transform="rotate({rotate} {f(x)} {f(y)})"' if rotate is not None else ""
    return label(x, y, text, cls, "middle", extra)


def draw():
    """The <svg>, as markup."""
    cx, cy = xy(*T["chateau"])
    ax, ay = xy(*T["airport"])
    km = 20 / 111.2 * K
    bx = xy(S_LAT, 1.905)[0]
    routes = "\n".join(
        f'    <path class="g-rmap__case" d="{path(p)}"/>\n'
        f'    <path class="g-rmap__r g-rmap__r--{k}" data-route="{k}" d="{path(p)}"/>'
        for k, p in ROUTES.items())
    towns = "".join([
        town("toulouse", "Toulouse", dx=10, dy=6, cls="g-rmap__city", r=4.5),
        town("pamiers", "Pamiers", dx=-9, anchor="end"),
        town("foix", "Foix", dx=-9, anchor="end"),
        town("tarascon", "Tarascon", dx=-9, anchor="end"),
        town("ax", "Ax-les-Thermes", dx=9, dy=6),
        town("mirepoix", "Mirepoix", dx=0, dy=-10, anchor="middle"),
        town("carcassonne", "Carcassonne", dx=-2, dy=-13, anchor="end",
             cls="g-rmap__city", r=4.5),
        town("andorra", "Andorra la Vella", dx=0, dy=20, anchor="middle"),
        town("pasdelacase", "Pas de la Case", dx=8, dy=17),
        town("puigcerda", "Puigcerdà", dx=10, dy=5),
        town("cabannes", "Les Cabannes", dx=-14, dy=24, anchor="end",
             cls="g-rmap__town g-rmap__town--small", r=2.6),
    ])
    shields = "".join([shield(43.505, 1.53, "A61"), shield(43.30, 1.675, "A66"),
                       shield(42.92, 1.56, "N20"), shield(42.69, 1.88, "N20"),
                       shield(42.565, 1.77, "N22"), shield(42.388, 1.845, "C-16")])
    names = "".join([
        place_label(43.33, 1.98, "FRANCE", "g-rmap__country"),
        place_label(42.618, 1.5, "ANDORRA", "g-rmap__country g-rmap__country--small"),
        place_label(42.385, 2.24, "SPAIN", "g-rmap__country g-rmap__country--small"),
        place_label(43.27, 1.548, "Ariège", "g-rmap__river", rotate=-72),
        place_label(43.39, 1.348, "Garonne", "g-rmap__river", rotate=-62),
    ])
    peak_marks = "\n      ".join(peaks())
    return f'''<svg class="g-rmap__svg" viewBox="0 0 600 {H}" role="img" aria-labelledby="g-rmap-t g-rmap-d">
    <title id="g-rmap-t">Château de Gudanes and the roads to it</title>
    <desc id="g-rmap-d">A map of the Ariège and the eastern Pyrenees. The château is in the
      valley above Tarascon, beside Les Cabannes. From Toulouse the A61, the A66 and the N20
      run south through Pamiers, Foix and Tarascon; from Carcassonne the road comes west by
      Mirepoix to join it at Pamiers. From Andorra the road crosses at Pas de la Case and from
      Barcelona at Puigcerdà, and both come down the valley through Ax-les-Thermes. The
      railway from Toulouse follows the valley to Les Cabannes.</desc>
    <rect class="g-rmap__ground" x="0" y="0" width="600" height="{H}"/>
    <g class="g-rmap__peaks" aria-hidden="true">
      {peak_marks}
    </g>
    <path class="g-rmap__andorra" d="{path(ANDORRA, smooth=False)} Z"/>
    <path class="g-rmap__border" d="{path(BORDER_W, smooth=False)}"/>
    <path class="g-rmap__border" d="{path(BORDER_E)}"/>
    <path class="g-rmap__water" d="{path(GARONNE)}"/>
    <path class="g-rmap__water" d="{path(ARIEGE)}"/>
    {names}
    <path class="g-rmap__rail" d="{path(RAIL)}"/>
{routes}
    {shields}
    {towns}
    <g class="g-rmap__plane" transform="translate({f(ax)} {f(ay)})" aria-hidden="true">
      <circle r="9"/>
      <path d="M0 -6 L1.3 -1.2 L6 1.2 L6 2.4 L1.3 1.2 L1 4.6 L2.6 5.8 L2.6 6.6 L0 5.8 L-2.6 6.6 L-2.6 5.8 L-1 4.6 L-1.3 1.2 L-6 2.4 L-6 1.2 L-1.3 -1.2 Z"/>
    </g>
    <text x="{f(ax)}" y="{f(ay + 26)}" class="g-rmap__town g-rmap__town--small" text-anchor="middle">airport</text>
    <g class="g-rmap__chateau" transform="translate({f(cx)} {f(cy)})">
      <circle class="g-rmap__halo" r="22"/>
      <circle class="g-rmap__ring" r="13"/>
      <path d="M-9 6 h18 v-11 l-4 -3.5 v-5 h-2.8 v2.8 l-2.2 -2.2 l-2.2 2.2 v-2.8 h-2.8 v5 l-4 3.5 z"/>
    </g>
    <path class="g-rmap__leader" d="M{f(cx + 12)} {f(cy - 14)} L{f(cx + 26)} {f(cy - 30)}"/>
    <g class="g-rmap__tag" transform="translate({f(cx + 30)} {f(cy - 50)})">
      <text class="g-rmap__tag-h">Château de Gudanes</text>
      <text class="g-rmap__tag-s" y="19">Château-Verdun</text>
    </g>
    <text class="g-rmap__off" x="{f(bx + 12)}" y="{H - 12}">to Barcelona</text>
    <g class="g-rmap__card" transform="translate(366 24)">
      <rect width="212" height="78"/>
      <rect class="g-rmap__card-in" x="4" y="4" width="204" height="70"/>
      <text x="106" y="34" text-anchor="middle" class="g-rmap__card-h">The Ariège</text>
      <text x="106" y="56" text-anchor="middle" class="g-rmap__card-s">and the roads to Gudanes</text>
    </g>
    <g class="g-rmap__compass" transform="translate(528 650)" aria-hidden="true">
      <circle r="20"/>
      <path class="g-rmap__needle" d="M0 -16 L5 0 L0 16 L-5 0 Z"/>
      <path d="M0 -16 L5 0 L-5 0 Z"/>
      <text y="-26" text-anchor="middle">N</text>
    </g>
    <g class="g-rmap__scale" transform="translate({f(528 - km / 2)} 712)" aria-hidden="true">
      <path d="M0 0 H{f(km)} M0 -4 V4 M{f(km / 2)} -3 V3 M{f(km)} -4 V4"/>
      <text x="{f(km / 2)}" y="19" text-anchor="middle">20 km</text>
    </g>
  </svg>'''


def main():
    with io.open(TEMPLATE, encoding="utf-8") as fh:
        text = fh.read().replace("\r\n", "\n")
    if BEGIN not in text or END not in text:
        print("the markers are missing from " + TEMPLATE)
        return 1
    head, rest = text.split(BEGIN, 1)
    _old, tail = rest.split(END, 1)
    new = head + BEGIN + "\n  " + draw() + "\n  " + END + tail
    if new == text:
        print("already up to date")
        return 0
    with io.open(TEMPLATE, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    print("drawn")
    return 0


if __name__ == "__main__":
    sys.exit(main())
