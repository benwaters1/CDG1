# The register, applied everywhere — not just the homepage

I fixed the funder line on Home last round, then checked whether the same
faults were sitting on the other pages. Two were.

## The funder line was still live on Restoration

> *"The five finished rooms are what pays for it, and they are open now."*

The exact sentence I cut from the homepage, still telling a guest their room
is a funding mechanism. Now: **"Five rooms are finished, and they are open
now."**

## Two pages defined the house by what it lacks

Workshops: *"There is no reception desk, no lift, and no turndown service."*
Three absences in a row measures the château against a hotel and comes up
short on a scale that does not apply.

Now: **"There is no reception, because someone comes out to meet you."** Same
fact, stated as a choice.

Stay had *"a stay with real limitations"* — now **"It is an old house and
behaves like one."**

## The linen was missing from the page that sells the rooms

Stay said: *"the beds, the linen, the floors underfoot, the bathrooms."* A
list of nouns. **The word "embroidered" appeared zero times on that page.**

Hand-embroidered vintage linen, collected over years rather than ordered by
the crate, is the strongest luxury signal you have — and it was one word in a
list. Now:

> The beds are very good ones, dressed in hand-embroidered vintage linen found
> and collected over years rather than ordered by the crate — **so no two
> rooms are made up the same.**

## Three flags I did NOT act on, and why

**"Putting a modern surface over it would be illegal"** and **"protected under
French law and cannot be changed"** — my check read *unfinished* and *not a
hotel* as apology. They are the opposite: those sentences are a flex. Kept.

**And the list of absences in `_before.html` stays**, because it sits under
the heading *"Not if you want a hotel."* Under a self-selection heading a list
of absences is **filtering, which is confident** — and the line that follows
it is the whole proposition: *"Someone will meet you and then leave you
alone."*

The audit rule now knows the difference: absences inside a self-selection
block pass, the same words in running prose fail.

## New audit rules

  - the guest framed as funding the restoration
  - value language
  - the house defined by what it lacks, outside a self-selection block

## Testing

242 renders, 11 conditions: 3, all text-measure boundaries within a character.
