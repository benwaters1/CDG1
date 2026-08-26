# French and Spanish are offered but the pages are in English

The switcher offers `en` / `fr` / `es`, and `translations.py` holds 357 strings
that are properly translated. But those cover the **chrome** — navigation,
buttons, form labels, status words — not the prose.

Measured across the guest templates:

| | |
|---|---|
| `t()` calls in guest templates | **172** |
| Visible words that never call `t()` | **10,713** |

Per page, the worst:

| page | words | `t()` calls |
|---|---|---|
| book_rooms | 1,246 | **0** |
| restoration | 1,160 | **0** |
| workshops_public | 1,150 | **0** |
| restaurant_info | 678 | **0** |
| events_info | 621 | **0** |
| workshop_detail | 539 | **0** |

So a visitor who picks **Français** gets a French menu, French buttons, French
form labels — wrapped around an entirely English page. That reads worse than
offering only English, because it looks broken rather than monolingual.

## Three options, in order of what I would actually do

### 1. Take the switcher down until the prose is translated

One line in the template. The site is honest again immediately, and nobody is
promised a language the site cannot deliver. Everything already translated
keeps working for the till and the staff screens, which is where those 357
strings are genuinely used.

### 2. Translate the prose properly, page by page

About 10,700 words. Machine translation will not do — the register is the whole
point, and "a centimetre is a good morning" translated badly is worse than the
English. This needs a French speaker who can hear the voice, and the Spanish
after. Realistically a paid job, and worth doing *after* launch rather than
holding launch for it.

### 3. Translate the top of the funnel only

Home, Stay and the booking flow — roughly 2,600 words. A visitor browsing in
French can understand what is offered and complete a booking; the long-form
pages stay English with a line saying so. Cheaper, and it covers the pages that
actually convert.

**My recommendation: (1) now, (3) before you push into French-speaking markets,
(2) eventually.** Launching with a broken language switcher is a worse first
impression than launching in English.

## If you take option 1

In `public_base.html`, the language switcher is `.g-lang`. Hiding it needs no
route change — `current_language()` falls back to English and every `t()` call
returns its source string.

The 357 existing translations stay in place and keep working for the POS and
staff screens, so nothing is lost.
