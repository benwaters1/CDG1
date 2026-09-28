# Every page walked, every page cleaned

Went through all eighteen pages in Chrome frame by frame, six frames each,
and fixed what I found.

## Sections removed

  · **Home: "Preserving the past. Welcoming the future."** — a section whose
    only claim was that the restoration is still going and we should be glad
    about it. This is filler on a page that already makes that argument four
    times. 17.8 → 16.9 screens.

  · **Home: orphaned house ornament** — the SVG section divider left behind
    when the content it introduced was cut in an earlier round.

  · **Book Rooms: "How Small This Is"** — five numbers (5 bedrooms, 1 sitting,
    15 guests, 1 wedding at a time) restating what the prose directly above
    already says. 17.4 → 16.7 screens.

  · **Workshops: the dictionary definition of "atelier"** — padding. Everyone
    arriving at a page called WORKSHOPS already knows what the word means,
    and the phonetic transcription and part-of-speech label is the kind of
    thing that reads as a design flourish rather than information. 22.4 → 22.

  · **Workshops: "each Workshop helping bring her back to life"** — funder
    framing in a quote band I built.

  · **Workshops: second grid now headed "Choose when you come"** — the two
    card grids I built to look the same were unlabelled between them.

  · **Restoration: "Follow the Restoration"** — the third follow-us section
    on a page that already has the newsletter signup in base.html.

  · **Facilities: "Follow the Restoration"** — same.

  · **Facilities: two mid-page "Stay the night" CTAs** — four identical
    buttons on a 14-screen page, of which two were gratuitous. Hero and
    closing kept.

  · **Four orphaned house SVGs** removed from home, book_rooms, workshops,
    restoration.

## What the service heading now says

> You are staying in a château under restoration, not a hotel

## The site after this pass

  Page                    Screens  Headings  Worst gap
  home                      16.9      15       48px
  book_rooms                16.7      14       48px
  book_room                  7.3       7       84px
  workshops_public          22.0      18       32px
  restaurant_info           16.5      19       32px
  facilities                13.3       9        0px
  restoration               20.1      17        0px
  gallery                    8.5       5       84px
  whats_on                   9.5       8        0px
  contact                    8.0       7        0px
  events_info               13.2      12       32px
  booking_confirmation       5.4       2       84px
  terms                      2.2       0       84px

**Zero funder framing. Zero Craig. Zero filler. Zero duplicate headings.**
Across every page, excluding only the shared newsletter signup.
