#!/usr/bin/env python3
"""Render every template with a fixture so it can be measured in a browser.

Rebuilt after the container reset wiped the working directory. The templates
and CSS came back from the last zip; this file did not, so it is written from
scratch — which means it is NOT the harness that produced earlier numbers and
any figure from it should be read as a fresh measurement rather than a
continuation.
"""
import os, re, glob, json, datetime
from jinja2 import Environment, FileSystemLoader, Undefined

SRC = 'ALL/templates'
CSS = 'ALL/static/gudanes.css'
OUT = 'review'


class Quiet(Undefined):
    """A missing value renders as nothing rather than exploding, so one gap in
    the fixture does not cost a whole page."""
    def __getattr__(self, n): return Quiet()
    def __getitem__(self, n): return Quiet()
    def __call__(self, *a, **k): return Quiet()
    def __iter__(self): return iter([])
    def __str__(self): return ''
    def __len__(self): return 0
    def __bool__(self): return False
    def __float__(self): return 0.0
    def __int__(self): return 0


class R(dict):
    """Attribute access over a dict, so `booking.total` and `booking['total']`
    both work — templates use both."""
    def __getattr__(self, k):
        try: return self[k]
        except KeyError: return Quiet()
    def get(self, k, d=None): return dict.get(self, k, d)


def url_for(endpoint, **kw):
    if endpoint == 'static':
        return '/static/' + kw.get('filename', '')
    return '/' + endpoint.replace('_page', '').replace('_', '-')


ROOMS = [R({'id': i, 'name': n, 'slug': n.lower().replace(' ', '-'),
            'price_per_night': p, 'sleeps': 2, 'from_price': p})
         for i, (n, p) in enumerate(
             [('Chambre Émeraude', 220), ('Chambre du Levant', 260),
              ('Les Deux Chambres', 300), ('Chambre Cerise', 240),
              ('Chambre Bleue', 280)], start=1)]

BK = R({'reference_code': 'GUD-4417', 'status': 'Confirmed',
        'room_name': 'Chambre Émeraude', 'nights': 3, 'guests': 2,
        'rate_per_night': 220, 'rooms_total': 660, 'tourist_tax': 6.60,
        'total': 1236.60, 'paid': 0, 'balance': 1236.60,
        'manage_url': 'https://chateaugudanes.com/b/abc',
        'arrival_date': '2026-09-11', 'departure_date': '2026-09-14',
        'guest_name': 'Whitcombe', 'email': 'a@b.com',
        'extras': [R({'label': 'Dinner at La Table, two guests', 'amount': 190}),
                   R({'label': 'Transfer from Toulouse, both ways', 'amount': 380})]})

WS = R({'id': 1, 'title': 'Plaster and Lime', 'price_per_person': 2400,
        'duration_label': 'Five days, four nights', 'places': 15,
        'start_date': '2026-05-10', 'end_date': '2026-05-15', 'spaces_left': 4})

CTX = dict(
    url_for=url_for,
    request=R({'path': '/', 'args': R({}), 'endpoint': 'home'}),
    settings=R({'lat': '42.7847', 'lng': '1.6564', 'cancel_free_days': 30,
                'phone': '+33 5 61 00 00 00', 'email': 'bonjour@chateaugudanes.com'}),
    house_rooms=R({'from_price': 220, 'count_word': 'five', 'count': 5}),
    rooms=ROOMS, room=ROOMS[0], availability={r['id']: True for r in ROOMS},
    booking=BK, bookings=[BK], reservation=BK, reservations=[BK],
    workshop=WS, workshops=[WS], workshop_dates=[WS],
    sessions_by_workshop={1: [R({'session': R({'start_date': '2026-05-10', 'end_date': '2026-05-15'}),
                                 'remaining': 4, 'capacity': 15})]},
    atelier_reviews=[], featured_reviews=[],
    guest=BK, profile=BK, statements=[], upcoming=[], past=[],
    reveal_pairs=[], dinner_dates=['2026-09-11', '2026-09-18'],
    booked_dates=['2026-09-20'], errors={}, form=R({}), csrf_token=lambda: 'x',
    now=datetime.datetime.now(), today=datetime.date.today(),
    current_user=R({'is_authenticated': False}),
)


def t(key, *a, **k):
    """The app's translation helper. Returns the key so copy checks still see
    real words, and accepts the kwargs the merged code passes."""
    return key


def main():
    env = Environment(loader=FileSystemLoader(SRC), undefined=Quiet)
    env.globals.update(CTX)
    env.globals['t'] = t
    env.globals['_'] = t
    env.globals['gettext'] = t
    env.filters['date_short'] = lambda x, *a: str(x)
    env.filters['datefmt'] = lambda x, *a: str(x)
    # Filters the app defines that this harness does not have. Naming them
    # explicitly rather than catching everything, so a genuinely unknown
    # filter still fails loudly.
    for f in ('house_day','money','eur','nl2br','slugify','shortdate','pretty'):
        env.filters[f] = lambda x, *a, **k: str(x)
    env.globals['date_range'] = lambda a, b: f'{a} – {b}'

    os.makedirs(OUT, exist_ok=True)
    css = open(CSS, encoding='utf-8').read()
    ok = bad = 0
    fails = []
    for path in sorted(glob.glob(f'{SRC}/*.html')):
        name = os.path.basename(path)
        if name.startswith('_'):
            continue                      # macros, not pages
        try:
            html = env.get_template(name).render(**CTX)
        except Exception as e:
            bad += 1
            fails.append(f'{name}: {str(e)[:60]}')
            continue
        # inline the stylesheet so a file:// render needs nothing else
        if '<style>' in html:
            html = re.sub(r'<style>\n.*?\n</style>',
                          '<style>\n' + css + '\n</style>', html, count=1, flags=re.S)
        else:
            html = '<style>\n' + css + '\n</style>\n' + html
        open(f'{OUT}/{name}', 'w', encoding='utf-8').write(html)
        ok += 1
    print(f'rendered {ok}/{ok + bad}')
    for f in fails[:6]:
        print('  FAIL', f)


if __name__ == '__main__':
    main()
