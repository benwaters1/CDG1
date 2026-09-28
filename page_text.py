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
    "Atelier dates": "Dates des ateliers",
    "The restoration itself": "La restauration elle-même",
    "See what it looks like first": "Voir à quoi cela ressemble",
    "Subscribe": "S'abonner",
    "Contact us": "Nous contacter",
    "Groups": "Groupes",

    # -- Stay: the promise made above the fold --------------------------
    "The rooms open one at a time, as the restoration reaches them.":
        "Les chambres ouvrent une à une, à mesure que la restauration les "
        "atteint.",

    # -- Stay: what the walls are, which is the thing guests ask about --
    "Not a Compromise": "Non pas un compromis",
    "Why the Walls Stay As They Are":
        "Pourquoi les murs restent tels quels",
    "The bare plaster in these rooms is not unfinished work. It is protected, "
    "and putting a modern surface over it would be illegal.":
        "Le plâtre nu de ces chambres n'est pas un travail inachevé. Il est "
        "protégé, et le recouvrir d'un enduit moderne serait illégal.",
    "New work is done so it could one day be removed without harming what was "
    "there":
        "Toute intervention nouvelle est faite pour pouvoir être retirée un "
        "jour sans abîmer ce qui était là",
    "Every surface is photographed and documented before anyone touches it":
        "Chaque surface est photographiée et documentée avant que quiconque y "
        "touche",
    "Lime, not cement": "De la chaux, pas du ciment",
    "The building has to breathe. Cement traps water and destroys what it "
    "covers":
        "Le bâtiment doit respirer. Le ciment retient l'eau et détruit ce "
        "qu'il recouvre",
    "Approved, then done": "Autorisé, puis réalisé",
    "Not the other way round. Unauthorised work on a Class I monument is a "
    "criminal offence":
        "Et non l'inverse. Des travaux non autorisés sur un monument classé "
        "sont un délit",
}

ES = {
    # -- The shell every public page carries ----------------------------
    "What would you like to hear about?": "¿Sobre qué le gustaría saber?",
    "Rooms and availability": "Habitaciones y disponibilidad",
    "Atelier dates": "Fechas de los talleres",
    "The restoration itself": "La restauración en sí",
    "See what it looks like first": "Ver primero cómo es",
    "Subscribe": "Suscribirse",
    "Contact us": "Contactar con nosotros",
    "Groups": "Grupos",

    # -- Stay: the promise made above the fold --------------------------
    "The rooms open one at a time, as the restoration reaches them.":
        "Las habitaciones se abren una a una, a medida que la restauración "
        "llega a ellas.",

    # -- Stay: what the walls are, which is the thing guests ask about --
    "Not a Compromise": "No es una renuncia",
    "Why the Walls Stay As They Are":
        "Por qué las paredes siguen como están",
    "The bare plaster in these rooms is not unfinished work. It is protected, "
    "and putting a modern surface over it would be illegal.":
        "El yeso desnudo de estas habitaciones no es una obra sin terminar. "
        "Está protegido, y cubrirlo con un acabado moderno sería ilegal.",
    "New work is done so it could one day be removed without harming what was "
    "there":
        "Toda intervención nueva se hace de modo que algún día pueda "
        "retirarse sin dañar lo que había",
    "Every surface is photographed and documented before anyone touches it":
        "Cada superficie se fotografía y documenta antes de que nadie la toque",
    "Lime, not cement": "Cal, no cemento",
    "The building has to breathe. Cement traps water and destroys what it "
    "covers":
        "El edificio tiene que respirar. El cemento retiene el agua y destruye "
        "lo que cubre",
    "Approved, then done": "Autorizado, y después ejecutado",
    "Not the other way round. Unauthorised work on a Class I monument is a "
    "criminal offence":
        "No al revés. Las obras no autorizadas en un monumento de Clase I son "
        "un delito",
}
