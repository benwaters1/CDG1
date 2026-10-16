# For whoever hands over the public templates: two rooms' beds, and where a room's facts come from

One public partial had two rooms described wrongly, and the fix is in
`_room_copy.html`. Please keep it when you next send that file.

**1. The Queen Room has a king bed, and the Twin Room's two beds are doubles**
(owner, 15 October 2026). Your own long copy for the Queen Room already said
"A king bed in the Chambre Olympe de Gouges", but the short label beside the
name, in `room_note`, still said "a queen bed". The Twin Room's said "a twin
room". They now read:

    'The Twin Room':    'two double beds, with a private bathroom',
    'The Queen Room':   'a king bed, with a private bathroom',

The room's name stays "The Queen Room"; only what it is described as changes.

**2. The beds and the bathroom come from their own fields.** `bed_setup` and
`bathroom` on each room are what the owner sets, and your cards already read
them. The free-text `amenities` list no longer carries a bed or a bathroom
(it said "Shared bathroom" on two rooms after every room became private,
which `book_rooms.html` filtered out and `book_room.html` did not), and a
feature the owner ticks is stored as its words ("Mountain view"), never as a
key. So `book_room.html`'s tag list is safe to print as it stands.

`bathroom` is `private` or `shared`, which is what your templates test for.
For stays every room's is private: the bathrooms are on the ground floor, not
in the room, and the house sells no more than three rooms a night. For
workshops the bathrooms are shared and every room is two guests sharing,
which your workshop pages already say.
