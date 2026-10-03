# -*- coding: utf-8 -*-
"""No control characters in the source.

A backslash escape written through a shell heredoc can arrive as the control
character it names: \\b, meant as a regex word boundary, lands as 0x08,
backspace. The pattern still compiles and simply never matches, so nothing
errors and every check that leans on it passes. CLAUDE.md records it costing
check_handover four attempts; on 3 October a scan found it in three more
places that had been live for weeks -- redact_secrets in app.py (the
long-token redaction never ran), tools/fix_labels.py (its duplicate-id guard
was always empty), and a new check in test_booking_journey that therefore
could not fail.

So every tracked text file is read for characters below 0x20 other than tab,
line feed and carriage return. One is never what anybody meant to type.
"""
import os
import subprocess

from _harness import Suite
import _harness

ROOT = _harness.ROOT
TEXT = (".py", ".html", ".css", ".js", ".txt", ".md", ".json", ".toml", ".cfg",
        ".ini", ".xml", ".svg", ".sql", ".bat", ".sh", ".yml", ".yaml")
ALLOWED = {"\t", "\n", "\r"}


def control_characters(text):
    """[(line number, character code)] for every stray control character."""
    return [(n, ord(ch)) for n, line in enumerate(text.split("\n"), 1)
            for ch in line if ord(ch) < 32 and ch not in ALLOWED]


def run():
    s = Suite("No control characters in the source")
    listed = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", errors="replace").stdout.split("\n")
    files = [f for f in listed if f.endswith(TEXT)]
    s.check("the tracked text files were listed", len(files) > 500,
            detail="%d files; git ls-files found too few to be reading the tree" % len(files))
    found = {}
    for rel in files:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            hits = control_characters(fh.read())
        if hits:
            found[rel] = hits[:3]
    s.check("not one has a control character in it", not found,
            detail="%s -- a \\b or \\t written through a heredoc arrives as the "
                   "character itself, and a pattern carrying it compiles and "
                   "never matches" % sorted(found.items())[:4])

    s.section("And the sweep can see one")
    s.check("a backspace where a word boundary was meant is found",
            control_characters('re.sub(r"\x08[a-z]+\x08", "", s)') == [(1, 8), (1, 8)])
    s.check("a tab, a newline and a carriage return are not",
            control_characters("a\tb\r\nc\n") == [])
    return s


if __name__ == "__main__":
    print(run().report())
