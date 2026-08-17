"""Export the room and add-on catalogue to a file you can upload to the live site.

    python export_catalogue.py            writes catalogue.json
    python export_catalogue.py out.json   writes somewhere else

The local database and the live one are separate — the live one lives on the
hosting volume — so rooms entered here do not appear to guests. This copies the
catalogue across without retyping it, and without moving anything private:
guests, bookings, staff and payments are deliberately not included.

Upload the file at Management → Import catalogue on the live site. Running it
twice is safe; rooms are matched by name and updated rather than duplicated.
"""
import json
import os
import sqlite3
import sys

# The database in the folder you run this from, NOT the one beside the script.
# Those differ when the script is copied between checkouts, and reading the wrong
# one exported a single test room instead of the real five. Printed below so a
# mistake is visible rather than silent.
DB = os.environ.get("GUDANES_DB_PATH") or os.path.join(os.getcwd(), "gudanes_hr.db")

# Everything that describes a room, minus the id (the live database has its own)
# and export_token, which is a per-installation secret and must not be copied.
ROOM_FIELDS = [
    "name", "description", "max_occupancy", "price_per_night", "active",
    "sort_order", "amenities", "min_nights", "max_adults", "max_children",
    "size_sqm", "bed_setup", "bathroom", "outlook", "floor",
]
EXTRA_FIELDS = [
    "name", "price", "active", "sort_order", "category", "description",
    "lead_time_days", "max_qty", "guest_bookable", "sold_in_pos",
]


def columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def main(argv):
    if not os.path.exists(DB):
        print(f"No database at {DB}")
        return 1
    out_path = argv[1] if len(argv) > 1 else "catalogue.json"
    print(f"Reading {DB}")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    def dump(table, fields):
        have = columns(conn, table)
        use = [f for f in fields if f in have]
        rows = [dict((f, r[f]) for f in use)
                for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
        skipped = [f for f in fields if f not in have]
        if skipped:
            print(f"  ({table}: this database has no {', '.join(skipped)} — skipped)")
        return rows

    data = {"rooms": dump("rooms", ROOM_FIELDS), "extras": dump("extras", EXTRA_FIELDS)}
    conn.close()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)

    print(f"\nWrote {out_path}")
    for room in data["rooms"]:
        price = room.get("price_per_night") or 0
        print(f"  room   {room['name'][:38]:<40} EUR {price:>7.0f}")
    for extra in data["extras"]:
        print(f"  add-on {extra['name'][:38]:<40} EUR {(extra.get('price') or 0):>7.2f}")
    print("\nNothing private is in this file — no guests, bookings, staff or payments.")
    print("Upload it at Management > Import catalogue on the live site.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
