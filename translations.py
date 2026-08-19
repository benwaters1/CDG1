"""French and Spanish for the guest-facing site.

Why a dictionary and not Babel: Babel wants .po files compiled to .mo, which is
a build step. This app deliberately has none — it is cloned, `python app.py`,
and it runs. A dictionary keyed on the English source keeps that true, keeps
the translations reviewable in a normal diff, and needs no tooling to change a
word.

The cost of keying on the English string is that editing English copy silently
drops back to English rather than showing a stale translation. That is the safe
direction to fail, and tests/test_translations.py reports how much of each
language is filled in so the gap is visible rather than assumed.

Only the guest-facing site is translated. The staff app stays in English: it is
used by a handful of named people who share a working language, and translating
it would double the surface for no reader.

Accents matter here and are written properly — "Château", "Ariège", "Señor".
A missing entry falls back to English, so a partial language is always safe to
ship.
"""

# Languages offered in the switcher. English is the source and needs no table.
LANGUAGES = {
    "en": "English",
    "fr": "Français",
    "es": "Español",
}

FR = {
    # -- Navigation and shell -------------------------------------------
    "Stay": "Séjourner",
    "Stay Now": "Réserver un séjour",
    "Workshops": "Ateliers",
    "Weddings & Events": "Mariages et événements",
    "Private Photo & Videoshoots": "Séances photo et vidéo privées",
    "Gallery & Story": "Galerie et histoire",
    "Inside the Château": "À l'intérieur du château",
    "The house as she stands today": "La maison telle qu'elle est aujourd'hui",
    "The Château & Its Grounds": "Le château et son parc",
    "The Restoration Story": "L'histoire de la restauration",
    "Life at the Château": "La vie au château",
    "Gallery Overview": "Aperçu de la galerie",
    "The Views": "Les vues",
    "About & History": "À propos et histoire",
    "Press": "Presse",
    "Manage Booking": "Gérer ma réservation",
    "Menu": "Menu",
    "Close menu": "Fermer le menu",
    "Subscribe to our newsletter": "Abonnez-vous à notre lettre d'information",
    "Subscribe": "S'abonner",
    "Email": "E-mail",
    "Email address": "Adresse e-mail",
    "Contact us": "Nous contacter",
    "Groups": "Groupes",
    "Change or cancel a reservation": "Modifier ou annuler une réservation",
    "Terms & Conditions": "Conditions générales",
    "Privacy Policy": "Politique de confidentialité",
    "Visit": "Visiter",
    "Nightly stays": "Séjours à la nuitée",
    "La Table": "La Table",
    "The restoration": "La restauration",
    "Gallery": "Galerie",
    "Follow": "Nous suivre",
    "Manage a booking": "Gérer une réservation",
    "Stay the Night": "Passez la nuit",
    "Book": "Réserver",
    "Display currency": "Devise d'affichage",
    "Currency": "Devise",
    "Language": "Langue",
    "charged in EUR": "débité en EUR",

    # -- Booking ---------------------------------------------------------
    "Your dates": "Vos dates",
    "Arrival": "Arrivée",
    "Departure": "Départ",
    "Guests": "Voyageurs",
    "Who is coming": "Qui séjourne",
    "Your name": "Votre nom",
    "Phone": "Téléphone",
    "Phone (optional)": "Téléphone (facultatif)",
    "Anything else we should know?": "Autre chose à nous signaler ?",
    "Book this room": "Réserver cette chambre",
    "Request to book": "Demander à réserver",
    "night": "nuit",
    "nights": "nuits",
    "per night": "par nuit",
    "Total": "Total",
    "Still to pay": "Reste à payer",
    "Paid in full — thank you.": "Intégralement réglé — merci.",
    "Reference": "Référence",
    "Reference code": "Code de réservation",
    "Status": "Statut",
    "Dates": "Dates",
    "Room": "Chambre",
    "Party size": "Nombre de personnes",

    # -- The availability calendar ---------------------------------------
    "Not available": "Non disponible",
    "Already booked": "Déjà réservé",
    "Booked on another channel": "Réservé sur un autre canal",
    "The château is full": "Le château est complet",
    "In the past": "Date passée",
    "Previous month": "Mois précédent",
    "Next month": "Mois suivant",
    "Now choose your departure day.": "Choisissez maintenant votre date de départ.",

    # -- Ateliers ---------------------------------------------------------
    "Register": "S'inscrire",
    "Room arrangement": "Type de chambre",
    "Per person, sharing a room.": "Par personne, en chambre partagée.",
    "Includes": "Comprend",
    "per person": "par personne",
    "places": "places",
    "Dates to be announced.": "Dates à venir.",

    # -- The guest's own account -----------------------------------------
    "Your château account": "Votre compte château",
    "Your stays": "Vos séjours",
    "Your ateliers": "Vos ateliers",
    "Your dinners": "Vos dîners",
    "Nothing booked at the moment.": "Aucune réservation pour le moment.",
    "Manage this stay": "Gérer ce séjour",
    "View this stay": "Voir ce séjour",
    "Manage this registration": "Gérer cette inscription",
    "Manage this booking": "Gérer cette réservation",
    "See everything you have with us →": "Voir toutes vos réservations →",
    "Facilities & Activities": "Ce que propose la maison",
    "A room, your own dates": "Une chambre, à vos dates",
    "Dining only — a table at La Table": "Dîner seulement — une table à La Table",
    "Five rooms — from €220 a night — breakfast included": "Cinq chambres — à partir de 220 € la nuit — petit-déjeuner compris",
    "Five rooms · from €220 a night · breakfast included": "Cinq chambres · à partir de 220 € la nuit · petit-déjeuner compris",
    "Three to seven nights · full board · itinerary included": "De trois à sept nuits · pension complète · programme compris",
    "Fixed dates, a small group": "Dates fixes, en petit groupe",
    "Manage an existing booking": "Gérer une réservation existante",
    "Stay in Winter": "Séjourner en hiver",
    "What's On": "À l'affiche",
    "Today": "Aujourd'hui",
    "Monday": "Lundi",
    "Tuesday": "Mardi",
    "Wednesday": "Mercredi",
    "Thursday": "Jeudi",
    "Friday": "Vendredi",
    "Saturday": "Samedi",
    "Sunday": "Dimanche",
}

ES = {
    # -- Navigation and shell -------------------------------------------
    "Stay": "Alojarse",
    "Stay Now": "Reservar estancia",
    "Workshops": "Talleres",
    "Weddings & Events": "Bodas y eventos",
    "Private Photo & Videoshoots": "Sesiones privadas de foto y vídeo",
    "Gallery & Story": "Galería e historia",
    "Inside the Château": "El interior del château",
    "The house as she stands today": "La casa tal como está hoy",
    "The Château & Its Grounds": "El château y sus jardines",
    "The Restoration Story": "La historia de la restauración",
    "Life at the Château": "La vida en el château",
    "Gallery Overview": "Vista general de la galería",
    "The Views": "Las vistas",
    "About & History": "Historia",
    "Press": "Prensa",
    "Manage Booking": "Gestionar mi reserva",
    "Menu": "Menú",
    "Close menu": "Cerrar menú",
    "Subscribe to our newsletter": "Suscríbase a nuestro boletín",
    "Subscribe": "Suscribirse",
    "Email": "Correo electrónico",
    "Email address": "Dirección de correo electrónico",
    "Contact us": "Contáctenos",
    "Groups": "Grupos",
    "Change or cancel a reservation": "Modificar o cancelar una reserva",
    "Terms & Conditions": "Términos y condiciones",
    "Privacy Policy": "Política de privacidad",
    "Visit": "Visitar",
    "Nightly stays": "Estancias por noche",
    "La Table": "La Table",
    "The restoration": "La restauración",
    "Gallery": "Galería",
    "Follow": "Síganos",
    "Manage a booking": "Gestionar una reserva",
    "Stay the Night": "Pase la noche",
    "Book": "Reservar",
    "Display currency": "Moneda",
    "Currency": "Moneda",
    "Language": "Idioma",
    "charged in EUR": "cobrado en EUR",

    # -- Booking ---------------------------------------------------------
    "Your dates": "Sus fechas",
    "Arrival": "Llegada",
    "Departure": "Salida",
    "Guests": "Huéspedes",
    "Who is coming": "Quién viene",
    "Your name": "Su nombre",
    "Phone": "Teléfono",
    "Phone (optional)": "Teléfono (opcional)",
    "Anything else we should know?": "¿Algo más que debamos saber?",
    "Book this room": "Reservar esta habitación",
    "Request to book": "Solicitar reserva",
    "night": "noche",
    "nights": "noches",
    "per night": "por noche",
    "Total": "Total",
    "Still to pay": "Pendiente de pago",
    "Paid in full — thank you.": "Pagado en su totalidad — gracias.",
    "Reference": "Referencia",
    "Reference code": "Código de reserva",
    "Status": "Estado",
    "Dates": "Fechas",
    "Room": "Habitación",
    "Party size": "Número de personas",

    # -- The availability calendar ---------------------------------------
    "Not available": "No disponible",
    "Already booked": "Ya reservado",
    "Booked on another channel": "Reservado en otro canal",
    "The château is full": "El château está completo",
    "In the past": "Fecha pasada",
    "Previous month": "Mes anterior",
    "Next month": "Mes siguiente",
    "Now choose your departure day.": "Ahora elija su fecha de salida.",

    # -- Ateliers ---------------------------------------------------------
    "Register": "Inscribirse",
    "Room arrangement": "Tipo de habitación",
    "Per person, sharing a room.": "Por persona, en habitación compartida.",
    "Includes": "Incluye",
    "per person": "por persona",
    "places": "plazas",
    "Dates to be announced.": "Fechas por confirmar.",

    # -- The guest's own account -----------------------------------------
    "Your château account": "Su cuenta del château",
    "Your stays": "Sus estancias",
    "Your ateliers": "Sus talleres",
    "Your dinners": "Sus cenas",
    "Nothing booked at the moment.": "No hay reservas por el momento.",
    "Manage this stay": "Gestionar esta estancia",
    "View this stay": "Ver esta estancia",
    "Manage this registration": "Gestionar esta inscripción",
    "Manage this booking": "Gestionar esta reserva",
    "See everything you have with us →": "Ver todas sus reservas →",
    "Facilities & Activities": "La casa y sus actividades",
    "A room, your own dates": "Una habitación, en sus fechas",
    "Dining only — a table at La Table": "Solo cena — una mesa en La Table",
    "Five rooms — from €220 a night — breakfast included": "Cinco habitaciones — desde 220 € la noche — desayuno incluido",
    "Five rooms · from €220 a night · breakfast included": "Cinco habitaciones · desde 220 € la noche · desayuno incluido",
    "Three to seven nights · full board · itinerary included": "De tres a siete noches · pensión completa · programa incluido",
    "Fixed dates, a small group": "Fechas fijas, en grupo reducido",
    "Manage an existing booking": "Gestionar una reserva existente",
    "Stay in Winter": "Alojarse en invierno",
    "What's On": "Qué hacer",
    "Today": "Hoy",
    "Monday": "Lunes",
    "Tuesday": "Martes",
    "Wednesday": "Miércoles",
    "Thursday": "Jueves",
    "Friday": "Viernes",
    "Saturday": "Sábado",
    "Sunday": "Domingo",
}

TABLES = {"fr": FR, "es": ES}


def translate(text, lang):
    """The translated string, or the English one if there isn't a translation.

    Falling back rather than raising is deliberate: a half-translated site is
    readable, and a page that breaks because somebody added a sentence is not.
    """
    if not text or lang not in TABLES:
        return text
    return TABLES[lang].get(text, text)


def coverage(lang):
    """(translated, total) against the fullest table, for the tests to report."""
    table = TABLES.get(lang)
    if table is None:
        return 0, 0
    keys = set()
    for other in TABLES.values():
        keys |= set(other)
    return sum(1 for k in keys if table.get(k)), len(keys)
