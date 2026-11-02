"""Diagnostic for the privacy callback. Run from the project root:

    uv run python debug_privacy.py

It only reads the database. Delete this file when done; do not commit it.
"""

import inspect
import platform
import sqlite3
import sys

print("Python :", sys.version.split()[0], platform.system(), platform.machine())
print("SQLite :", sqlite3.sqlite_version, "| module file:", sqlite3.__file__)

from app import callbacks, tools  # noqa: E402

print("app.tools     loaded from:", tools.__file__)
print("app.callbacks loaded from:", callbacks.__file__)
print("DB_PATH:", tools.DB_PATH, "| exists:", tools.DB_PATH.exists())
print("PROTECTED_COLUMNS:", sorted(tools.PROTECTED_COLUMNS))
print("callbacks.py is the fail-closed version:",
      "Fail closed" in inspect.getsource(callbacks))

QUERY = "SELECT first_name FROM patients"


def show_authorizer_calls(label, sql, fetch):
    calls = []

    def authorizer(action, arg1, arg2, db_name, trigger):
        calls.append((action, arg1, arg2, db_name, trigger))
        return sqlite3.SQLITE_OK

    conn = tools._connect_read_only()
    conn.set_authorizer(authorizer)
    try:
        cur = conn.execute(sql)
        fetch(cur)
        print(f"\n[{label}] authorizer was called {len(calls)} times:")
        for c in calls:
            print("   ", c)
    except Exception as e:
        print(f"\n[{label}] raised {type(e).__name__}: {e}")
    finally:
        conn.close()


print("\nSQLITE_READ constant =", sqlite3.SQLITE_READ, "| SQLITE_SELECT =", sqlite3.SQLITE_SELECT)
show_authorizer_calls("EXPLAIN", f"EXPLAIN {QUERY}", lambda c: c.fetchall())
show_authorizer_calls("plain SELECT", QUERY, lambda c: c.fetchone())

print("\nfind_protected_columns:", end=" ")
try:
    print(callbacks.find_protected_columns(QUERY))
except Exception as e:
    print(f"raised {type(e).__name__}: {e}")

conn = tools._connect_read_only()
print("\npatients columns in this database:",
      [r[1] for r in conn.execute("PRAGMA table_info(patients)")])
opts = [r[0] for r in conn.execute("PRAGMA compile_options")]
print("SQLite compile options mentioning AUTH/OMIT:",
      [o for o in opts if "AUTH" in o or "OMIT" in o] or "none")
conn.close()