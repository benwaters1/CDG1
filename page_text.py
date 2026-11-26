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
    "See what it looks like first": "Voir à quoi cela ressemble",
    "Subscribe": "S'abonner",
    "Groups": "Groupes",
    # -- Site review 25 Nov 2026: "Déjeuner et dîner à restaurants à proximité" -- the link that follows is
    #    "restaurants à proximité", so the words before it need the article: aux. -----------------
    "Lunch and dinner at the": "Déjeuner et dîner aux",
    # -- Site review 25 Nov 2026: the Family Suite's wallpaper, by name (owner: Antoinette Poisson
    #    made it as well as the bathrooms'). Written here so the French and Spanish pages change
    #    the same day as the English rather than waiting for the translation job. ----------------
    'Two bedrooms for a family, the Chambre Bleue and the Chambre des Fleurs, across 140 m², with bespoke flooring and every bed made up in luxury French linen. One of the two bedrooms has its original wallpaper, printed anew by Antoinette Poisson, in Paris, from its original templates. There are two double beds in one bedroom and three in the other, and children of eight and over are welcome.':
        'Deux chambres pour une famille, la Chambre Bleue et la Chambre des Fleurs, sur 140 m², avec des sols faits sur mesure et chaque lit fait de linge français de luxe. L’une des deux chambres a retrouvé son papier peint d’origine, réimprimé par Antoinette Poisson, à Paris, d’après ses planches d’origine. Il y a deux lits doubles dans une chambre et trois dans l’autre, et les enfants à partir de huit ans sont les bienvenus.',
    'Two bedrooms for a family, the Chambre Bleue and the Chambre des Fleurs, across 140 m², with bespoke flooring and the mountains at the windows. In one of them the original wallpaper has come home, printed anew by Antoinette Poisson, in Paris, from its original templates. Two double beds in one room and three in the other, each made up in luxury French linen: room for everyone, and children of eight and over are welcome.':
        'Deux chambres pour une famille, la Chambre Bleue et la Chambre des Fleurs, sur 140 m², avec des sols faits sur mesure et les montagnes aux fenêtres. Dans l’une d’elles, le papier peint d’origine est revenu, réimprimé par Antoinette Poisson, à Paris, d’après ses planches d’origine. Deux lits doubles dans une chambre et trois dans l’autre, chacun fait de linge français de luxe : de la place pour tous, et les enfants à partir de huit ans sont les bienvenus.',
    'Where large pieces of the original wallpaper were lost, it has been made again. In one of the Family Suite’s bedrooms, the original paper has been printed anew by Antoinette Poisson, in Paris, from its original templates. Smaller gaps are left bare, to keep the patina.':
        'Là où de grands morceaux du papier peint d’origine avaient disparu, il a été refait. Dans l’une des chambres de la Family Suite, le papier d’origine a été réimprimé par Antoinette Poisson, à Paris, d’après ses planches d’origine. Les petites lacunes restent nues, pour garder la patine.',
    # -- Second sweep, 26 Nov 2026 (BRIEF-sweep-and-code-review-26-nov.md). The French for the hero
    #    is the owner's own, word for word. -------------------------------------------------------
    'A restoration followed the world over, and a château to experience as it returns to life.':
        'Une restauration suivie dans le monde entier, et un château à vivre tandis qu’il renaît.',
    'Bruce in the hay meadow': 'Bruce dans la prairie de fauche',
    # The naming rule (owner, 26 Nov): a retreat is "un séjour", never "une retraite" or "un atelier", and the
    # standing descriptor is "l’expérience complète du château". A night is never called that.
    'Experience the château': 'Vivre le château',
    'The full château experience.': 'L’expérience complète du château.',
    'The full château experience, three to seven nights at a time.':
        'L’expérience complète du château, de trois à sept nuits.',
    'The full château experience, over three to seven nights.':
        'L’expérience complète du château, sur trois à sept nuits.',
    'A night: a room and breakfast. A retreat: the full château experience, with its gourmet chefs, every meal, and drinks included, the brocantes, the restoration, the valley, and a great deal more.':
        'Une nuit : une chambre et le petit-déjeuner. Un séjour : l’expérience complète du château, avec ses chefs gastronomiques, chaque repas, boissons comprises, les brocantes, la restauration, la vallée, et bien plus encore.',
    'Everything you have booked with us — stays, dinners, retreats — in one place. Tell us your email and we will send you a link. No password to remember.':
        'Tout ce que vous avez réservé chez nous — nuits, dîners, séjours — au même endroit. Indiquez votre adresse e-mail et nous vous enverrons un lien. Aucun mot de passe à retenir.',
    'The retreats are held in the middle of this work, not around it —':
        'Les séjours ont lieu au cœur de ce chantier, pas autour —',
    'A class at the château': 'Un cours au château',
    'the retreats, where you work on the building yourself': 'les séjours, où l’on travaille soi-même au bâtiment',
    'Retreat guest, summer 2024': 'Hôte d’un séjour, été 2024',
    'Retreat guest, autumn 2023': 'Hôte d’un séjour, automne 2023',
    # C2, Antique & French Finds: "un circuit des brocantes" (owner, 26 Nov). The words around <em>chiner</em>
    # and <em>vide-greniers</em> are separate runs of text, so each run has its own line.
    'A brocante and antiques tour: three days of': 'Un circuit des brocantes et des antiquaires : trois jours à',
    ', the art of the find': ', l’art de la trouvaille',
    'A three-day brocante tour of the markets, brocantes and':
        'Un circuit des brocantes de trois jours, par les marchés, les brocantes et les',
    'A guided tour of the brocantes and': 'Un circuit guidé des brocantes et des',
}

ES = {
    # -- The shell every public page carries ----------------------------
    "See what it looks like first": "Ver primero cómo es",
    "Subscribe": "Suscribirse",
    "Groups": "Grupos",
    # -- Site review 25 Nov 2026: the Family Suite's wallpaper, by name (owner: Antoinette Poisson
    #    made it as well as the bathrooms'). Written here so the French and Spanish pages change
    #    the same day as the English rather than waiting for the translation job. ----------------
    'Two bedrooms for a family, the Chambre Bleue and the Chambre des Fleurs, across 140 m², with bespoke flooring and every bed made up in luxury French linen. One of the two bedrooms has its original wallpaper, printed anew by Antoinette Poisson, in Paris, from its original templates. There are two double beds in one bedroom and three in the other, and children of eight and over are welcome.':
        'Dos dormitorios para una familia, la Chambre Bleue y la Chambre des Fleurs, en 140 m², con suelos hechos a medida y cada cama vestida con lujosa ropa de cama francesa. Uno de los dos dormitorios conserva su papel pintado original, reimpreso por Antoinette Poisson, en París, a partir de sus plantillas originales. Hay dos camas dobles en un dormitorio y tres en el otro, y los niños a partir de ocho años son bienvenidos.',
    'Two bedrooms for a family, the Chambre Bleue and the Chambre des Fleurs, across 140 m², with bespoke flooring and the mountains at the windows. In one of them the original wallpaper has come home, printed anew by Antoinette Poisson, in Paris, from its original templates. Two double beds in one room and three in the other, each made up in luxury French linen: room for everyone, and children of eight and over are welcome.':
        'Dos dormitorios para una familia, la Chambre Bleue y la Chambre des Fleurs, en 140 m², con suelos hechos a medida y las montañas en las ventanas. En uno de ellos ha vuelto el papel pintado original, reimpreso por Antoinette Poisson, en París, a partir de sus plantillas originales. Dos camas dobles en un dormitorio y tres en el otro, cada una vestida con lujosa ropa de cama francesa: sitio para todos, y los niños a partir de ocho años son bienvenidos.',
    'Where large pieces of the original wallpaper were lost, it has been made again. In one of the Family Suite’s bedrooms, the original paper has been printed anew by Antoinette Poisson, in Paris, from its original templates. Smaller gaps are left bare, to keep the patina.':
        'Donde se habían perdido grandes trozos del papel pintado original, se ha vuelto a hacer. En uno de los dormitorios de la Family Suite, el papel original ha sido reimpreso por Antoinette Poisson, en París, a partir de sus plantillas originales. Los huecos más pequeños se dejan al descubierto, para conservar la pátina.',
    # -- Second sweep, 26 Nov 2026 (BRIEF-sweep-and-code-review-26-nov.md). --------------------------
    'A restoration followed the world over, and a château to experience as it returns to life.':
        'Una restauración seguida en todo el mundo, y un castillo que vivir mientras vuelve a la vida.',
    'Bruce in the hay meadow': 'Bruce en el prado de siega',
    # The naming rule (owner, 26 Nov): a retreat is "un retiro", never "un taller", and the standing descriptor is
    # "la experiencia completa del castillo". A night is never called that.
    'Experience the château': 'Vivir el castillo',
    'The full château experience.': 'La experiencia completa del castillo.',
    'The full château experience, three to seven nights at a time.':
        'La experiencia completa del castillo, de tres a siete noches.',
    'The full château experience, over three to seven nights.':
        'La experiencia completa del castillo, durante tres a siete noches.',
    'A night: a room and breakfast. A retreat: the full château experience, with its gourmet chefs, every meal, and drinks included, the brocantes, the restoration, the valley, and a great deal more.':
        'Una noche: una habitación y el desayuno. Un retiro: la experiencia completa del castillo, con sus chefs gastronómicos, cada comida, bebidas incluidas, las brocantes, la restauración, el valle y mucho más.',
    'Everything you have booked with us — stays, dinners, retreats — in one place. Tell us your email and we will send you a link. No password to remember.':
        'Todo lo que ha reservado con nosotros — estancias, cenas, retiros — en un solo lugar. Indíquenos su correo electrónico y le enviaremos un enlace. Sin contraseña que recordar.',
    'The retreats are held in the middle of this work, not around it —':
        'Los retiros se celebran en medio de estas obras, no a su alrededor —',
    'A class at the château': 'Una clase en el castillo',
    'the retreats, where you work on the building yourself': 'los retiros, donde uno mismo trabaja en el edificio',
    'Retreat guest, summer 2024': 'Huésped de un retiro, verano de 2024',
    'Retreat guest, autumn 2023': 'Huésped de un retiro, otoño de 2023',
    # C2, Antique & French Finds: a brocante tour.
    'A brocante and antiques tour: three days of': 'Una ruta de brocantes y anticuarios: tres días de',
    ', the art of the find': ', el arte del hallazgo',
    'A three-day brocante tour of the markets, brocantes and':
        'Una ruta de brocantes de tres días por los mercados, las brocantes y los',
    'A guided tour of the brocantes and': 'Una ruta guiada por las brocantes y los',
}
