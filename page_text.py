# -*- coding: utf-8 -*-
"""The guest site's own sentences, in French and Spanish.

SEPARATE FROM translations.py FOR ONE REASON: volume. That file holds the
strings a template asks for by name — nav items, buttons, form labels, the
words the staff app uses — and it is meant to be read. This holds the prose of
the public pages, which is 1,900 sentences and climbing, and mixing the two
would bury the first in the second.

They are merged into one table at import, so there is still ONE lookup and one
answer to "what do we call this in French". A string that appears both as a
t() call and as page prose translates the same way in both places, which is
the point.

KEYED ON THE ENGLISH, whitespace-normalised, exactly as the page translator in
app.py normalises what it finds. That is what lets a translation survive the
design side redrawing a page: the key is the sentence, not the sentence at a
particular indentation inside a particular file.

WHAT IS DELIBERATELY NOT IN HERE.

Proper nouns. "Château de Gudanes", "La Table", "Cinq Chambres", the address,
the email. They are the same in every language and a translated address is a
guest writing to nobody.

The English glosses under French headings. The public pages carry pairs —
`Ce que la loi protège` with `What the law protects` set small beneath it —
and the gloss exists to help an English reader with the French. Translating it
into French would print the heading twice.

A missing entry falls back to English, so this file is always safe to be
partly filled. That is what makes it possible to ship a page at a time.
"""

FR = {
    # -- The shell every public page carries ----------------------------
    "What would you like to hear about?": "Que souhaitez-vous recevoir ?",
    "Rooms and availability": "Chambres et disponibilités",
    "Workshop dates": "Dates des ateliers",
    "The restoration itself": "La restauration elle-même",
    "See what it looks like first": "Voir à quoi cela ressemble",
    "Subscribe": "S'abonner",
    "Groups": "Groupes",
}

ES = {
    # -- The shell every public page carries ----------------------------
    "What would you like to hear about?": "¿Sobre qué le gustaría saber?",
    "Rooms and availability": "Habitaciones y disponibilidad",
    "Workshop dates": "Fechas de los talleres",
    "The restoration itself": "La restauración en sí",
    "See what it looks like first": "Ver primero cómo es",
    "Subscribe": "Suscribirse",
    "Groups": "Grupos",
}
