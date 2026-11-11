"""A log line never stops the code that wrote it (brief of 11 November 2026).

On 10 November 2026 the house calendar pass stopped part-way: a task subject
carried a tick mark, print() raised UnicodeEncodeError on the owner's cp1252
console, and nothing after that event synced. It was patched in that one
place. But app.py had 126 print() calls, most of them carrying a guest's name,
an email subject or Microsoft's and Stripe's own words, and the automation
loop and the calendar thread run in the same process as the pages. Any of
them could do it again, in a job or in a request.

So every line goes through log_line(), which never raises. This holds the app
to:

  NO print IN app.py. Not "no print of an f-string": a plain string today is
  an f-string after the next edit. Read from the compiled code rather than
  the text, so a comment or a string that mentions print( is not a hit, and
  `say = print` is. No allow-list, because there is nothing to allow.

  log_line ESCAPES WHAT THE CONSOLE CANNOT SHOW, and loses the line rather
  than raise for anything else: no console (pythonw), a closed pipe, an
  object whose str() raises.

  A JOB WHOSE ANSWER CARRIES A TICK STILL COMPLETES, under a strict cp1252
  stdout, and the job after it still runs. The same tick with print() put
  back is run beside it as this suite's own negative control: if that stops
  failing, the cp1252 stand-in has stopped standing in, and the check above
  it proves nothing.
"""
import dis
import io
import re
import sys
import types

from _harness import Suite, db
import _harness

m = _harness.m
TAG = "zz_log_line"
FIRST, SECOND, THIRD = f"{TAG}_first", f"{TAG}_second", f"{TAG}_third"
TICK = "\u2714"


def _app_functions():
    """The code of every function app.py defines, as the import compiled it.

    Not compiled again here: 6 MB of source takes three minutes on this
    machine, and the harness runs with PYTHONDONTWRITEBYTECODE so there is no
    cache to read. Every function, method and decorated view is reachable from
    the loaded module (through __wrapped__ for the decorated ones), and the
    code inside them -- nested functions, lambdas, comprehensions -- is in
    their constants, which _print_uses walks.
    """
    seen, codes = set(), []

    def take(obj):
        # request, g and session are proxies that raise when asked anything
        # outside a request; they hold no code of ours.
        try:
            follow(obj)
        except RuntimeError:
            pass

    def follow(obj):
        while obj is not None:
            fn = getattr(obj, "__func__", obj)
            code = getattr(fn, "__code__", None)
            if code is not None and code.co_filename == m.__file__ and id(code) not in seen:
                seen.add(id(code))
                codes.append(code)
            obj = getattr(fn, "__wrapped__", None)

    for value in list(vars(m).values()):
        take(value)
        if isinstance(value, type) and value.__module__ == m.__name__:
            for attr in vars(value).values():
                take(attr)
    for view in m.app.view_functions.values():
        take(view)
    return codes


def _names(codes):
    found = set()
    for code in codes:
        found.add(code.co_name)
        found |= _names([k for k in code.co_consts if isinstance(k, types.CodeType)])
    return found


def _print_lines():
    """And the text, for what runs at the top level and so is in no function:
    a print( at the start of any statement line."""
    with open(m.__file__, encoding="utf-8") as f:
        return [f"line {n}" for n, line in enumerate(f, 1)
                if re.match(r"\s*print\(", line)]


def _print_uses(code):
    found = []
    if "print" in code.co_names:
        for ins in dis.get_instructions(code):
            if ins.opname in ("LOAD_GLOBAL", "LOAD_NAME") and ins.argval == "print":
                found.append(f"{code.co_name} line {ins.positions.lineno}")
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            found.extend(_print_uses(const))
    return found


def _cp1252():
    """A stdout like the owner's console: cp1252, and strict about it."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict",
                            newline="\n", write_through=True)


def _written(stream):
    stream.flush()
    return stream.buffer.getvalue().decode("cp1252")


class _Settings(dict):
    def __missing__(self, key):
        return "0"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM automation_runs WHERE job_name LIKE ?", (f"{TAG}%",))
    conn.commit()
    conn.close()


def _tick_with(logger):
    """One automation pass over three stand-in jobs on a cp1252 stdout: the
    first answers with a tick, the second fails with one in its words (as
    Microsoft's and Stripe's errors quote a subject), the third is plain.
    Returns (raised, what was written, ran)."""
    ran = []

    def first(conn):
        ran.append(FIRST)
        return f"Tâche {TICK} faite pour Zoë"

    def second(conn):
        ran.append(SECOND)
        raise ValueError(f"Microsoft refused 'Ménage {TICK}'")

    def third(conn):
        ran.append(THIRD)
        return "done"

    saved = (m.AUTOMATION_JOBS, m.get_automation_settings, m.log_line, sys.stdout)
    _cleanup()
    out = _cp1252()
    raised = None
    try:
        m.AUTOMATION_JOBS = [(FIRST, f"{TAG}_on", None, 60, first),
                             (SECOND, f"{TAG}_on", None, 60, second),
                             (THIRD, f"{TAG}_on", None, 60, third)]
        m.get_automation_settings = lambda conn: _Settings({f"{TAG}_on": "1"})
        m.log_line = logger
        sys.stdout = out
        m.automation_tick()
    except Exception as e:  # noqa: BLE001 -- what escaped is the finding
        raised = e
    finally:
        m.AUTOMATION_JOBS, m.get_automation_settings, m.log_line, sys.stdout = saved
    return raised, _written(out), ran


def run():
    s = Suite("a log line never stops the code that wrote it")

    s.section("No print in app.py")
    codes = _app_functions()
    uses = [u for c in codes for u in _print_uses(c)] + _print_lines()
    s.check(f"nothing in app.py calls print -- log_line instead "
            f"({len(codes)} functions' code, and every line's text)",
            not uses, detail="; ".join(uses[:10]) + (f" ... and {len(uses) - 10} more"
                                                     if len(uses) > 10 else ""))
    s.check("and the scan can see one: a function that prints is found",
            _print_uses(compile("def f(x):\n    print(f'{x}')\n", "probe", "exec"))
            == ["f line 2"])
    s.check("a print( in a comment or a string is not one",
            not _print_uses(compile("# print(x)\nWORDS = 'print(x)'\n", "probe", "exec")))
    s.check("log_line is there to use", callable(getattr(m, "log_line", None)))
    s.check("the functions read include a job, a decorated page and a nested one",
            {"automation_tick", "sync_house_calendar", "refused", "add_occasion_item"}
            <= _names(codes),
            detail=str(len(codes)))

    s.section("log_line on a console that cannot show the character")
    out = _cp1252()
    try:
        m.log_line(f"[calendar] Microsoft refused task-1: Tâche {TICK}", file=out)
        ok, err = True, ""
    except Exception as e:  # noqa: BLE001
        ok, err = False, f"{type(e).__name__}: {e}"
    text = _written(out)
    s.check("a tick on cp1252 does not raise", ok, detail=err)
    s.check("it goes out escaped, and what cp1252 can show is kept as it is",
            text == "[calendar] Microsoft refused task-1: Tâche \\u2714\n", detail=repr(text))

    saved = sys.stdout
    out = _cp1252()
    try:
        sys.stdout = out
        m.log_line("Zoë", TICK, sep=" | ", end=".\n")
    finally:
        sys.stdout = saved
    s.check("stdout is the one in place at the call, and sep and end are print's",
            _written(out) == "Zoë | \\u2714.\n", detail=repr(_written(out)))

    utf8 = io.StringIO()
    m.log_line(f"Tâche {TICK}", file=utf8)
    s.check("a console that can show it gets it verbatim",
            utf8.getvalue() == f"Tâche {TICK}\n", detail=repr(utf8.getvalue()))

    s.section("And anything else loses the line, not the work")

    class Unprintable:
        def __str__(self):
            raise ValueError("no words")

        def __repr__(self):
            raise ValueError("none here either")

    class ClosedPipe:
        encoding = "utf-8"

        def write(self, text):
            raise OSError(32, "Broken pipe")

    class NoSuchCodec:
        encoding = "not-a-codec"

        def write(self, text):
            if not text.isascii():
                raise UnicodeEncodeError("ascii", text, 0, 1, "stand-in")
            self.got = text

    for label, call in (
            ("no console at all (pythonw: sys.stdout is None)", lambda: _no_stdout(m.log_line)),
            ("an object whose str() and repr() both raise", lambda: m.log_line(Unprintable())),
            ("a closed pipe", lambda: m.log_line("x", file=ClosedPipe())),
            ("a stream naming a codec that does not exist",
             lambda: m.log_line(TICK, file=NoSuchCodec()))):
        try:
            call()
            ok, err = True, ""
        except Exception as e:  # noqa: BLE001
            ok, err = False, f"{type(e).__name__}: {e}"
        s.check(f"{label}: does not raise", ok, detail=err)
    nsc = NoSuchCodec()
    m.log_line(TICK, file=nsc)
    s.check("and the unknown codec still gets the line, in ASCII",
            getattr(nsc, "got", None) == "\\u2714\n", detail=repr(getattr(nsc, "got", None)))

    s.section("A job whose answer carries a tick, on the owner's console")
    raised, text, ran = _tick_with(m.log_line)
    conn = db()
    rows = {r["job_name"]: r for r in conn.execute(
        "SELECT job_name, last_status, last_message FROM automation_runs WHERE job_name LIKE ?",
        (f"{TAG}%",)).fetchall()}
    conn.close()
    s.check("the automation pass completes", raised is None,
            detail=f"{type(raised).__name__}: {raised}" if raised else "")
    s.check("and every job after it still runs", ran == [FIRST, SECOND, THIRD], detail=str(ran))
    s.check("the ticked job is recorded as the success it was",
            FIRST in rows and rows[FIRST]["last_status"] == "ok"
            and TICK in (rows[FIRST]["last_message"] or ""),
            detail=str({k: dict(v) for k, v in rows.items()}))
    s.check("and its line reached the log, escaped",
            f"[automation] {FIRST}: Tâche \\u2714 faite pour Zoë" in text, detail=repr(text))
    s.check("the job that failed with a tick in its words is recorded with them",
            SECOND in rows and rows[SECOND]["last_status"] == "failed"
            and TICK in (rows[SECOND]["last_message"] or ""),
            detail=str({k: dict(v) for k, v in rows.items()}))
    s.check("and its line reached the log too",
            f"[automation] {SECOND} failed: Microsoft refused 'Ménage \\u2714'" in text,
            detail=repr(text))
    s.check("the plain job after both is recorded as ok",
            THIRD in rows and rows[THIRD]["last_status"] == "ok")

    s.section("Negative control: the same pass with print() put back")
    raised, text, ran = _tick_with(print)
    conn = db()
    first_row = conn.execute("SELECT last_status FROM automation_runs WHERE job_name = ?",
                             (FIRST,)).fetchone()
    conn.close()
    s.check("print() on the same console stops the pass (the stand-in console is strict)",
            isinstance(raised, UnicodeEncodeError),
            detail=f"{type(raised).__name__}: {raised}" if raised else "nothing raised")
    s.check("so the job after it never ran", ran == [FIRST, SECOND], detail=str(ran))
    s.check("and the job that worked was recorded as failed",
            first_row is not None and first_row["last_status"] == "failed",
            detail=str(dict(first_row) if first_row else None))
    _cleanup()
    return s


def _no_stdout(fn):
    saved = sys.stdout
    try:
        sys.stdout = None
        fn("nobody is listening")
    finally:
        sys.stdout = saved


if __name__ == "__main__":
    print(run().report())
