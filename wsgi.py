"""Production entry point. Gunicorn serves `app` from here.

Running `python app.py` is the development path, and everything inside that
file's `if __name__ == "__main__":` block — creating and migrating the
database, starting the automation loop — never runs under a WSGI server. So a
deployment pointed straight at `app:app` would boot with no tables at all and
500 on every page, and would never send a balance reminder, waitlist opening
or daily digest. This module is the difference.

Deliberately kept separate from app.py rather than doing this work at import:
importing the application should never start a thread that emails people, and
the test suite imports it.
"""
import os

from app import app, init_db, start_background_work

# Idempotent: creates the schema on a fresh volume and applies any pending
# migrations on an existing one. Must finish before the first request.
init_db()

# One automation loop per process. With more than one gunicorn worker every
# worker would run its own and guests would get each automated email once per
# worker, so the Procfile pins --workers 1 and scales with threads instead.
# GUDANES_NO_AUTOMATION=1 disables it — useful if a second instance is ever
# run purely to serve traffic.
if os.environ.get("GUDANES_NO_AUTOMATION", "0") != "1":
    start_background_work()
