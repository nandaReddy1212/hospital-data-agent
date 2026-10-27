# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import os
from zoneinfo import ZoneInfo

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "hospital.db"

MODEL = "gemini-3.8-flash"


def get_weather(query: str) -> str:
    """Simulates a web search. Use it get information on weather.

    Args:
        query: A string containing the location to get weather information for.

    Returns:
        A string with the simulated weather information for the queried location.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        city: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"

def get_table_row_count(table_name: str) -> dict:
    """Returns the number of rows in a table in the hospital database.

    Args:
        table_name: The table name, e.g. 'patients' or 'visits'.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        count = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        conn.close()
        return {"status": "success", "table": table_name, "rows": count}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def get_database_schema() -> dict:
    """"
    Returns all the tables in the hospital database and their columns.
    Call this before writing a SQL query so you know the exact column names.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        schema = {}
        for t in tables:
            cols = conn.execute(f'PRAGMA table_info("{t}")').fetchall()
            schema[t] = [{"column": c[1], "type": c[2]} for c in cols]
        conn.close()
        return {"status": "success", "schema": schema}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def run_sql_query(query: str) -> dict:
    """Runs a read-only SQL SELECT query on the hospital database and returns the results.

    Args:
        query: A single SQLite SELECT statement.
    """
    if not query.strip().lower().startswith("select"):
        return {"status": "error", "message": "Only SELECT queries are allowed."}
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cur = conn.execute(query)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(50)
        conn.close()
        return {"status": "success", "columns": columns, "rows": rows}
    except Exception as e:
        return {"status": "error", "message": str(e)}


root_agent = Agent(
    # Keep in sync with agents-cli-manifest.yaml: agents-cli derives this name
    # from the project `name:` recorded there, and telemetry reports it as
    # gen_ai.agent.name. Renaming the agent only here makes the two disagree,
    # and anything selecting traces by name stops finding this agent's.
    name="my_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
        api_key=os.environ.get("GEMINI_API_KEY"),
    ),
    instruction="""You are a hospital data assistant and data engineering tutor.
        You have access to a synthetic hospital SQLite database with tables:
        departments, doctors, patients, and visits.
        To answer questions about the data:
        1. Call get_database_schema to see the exact columns.
        2. Write a SELECT query and call run_sql_query. Use JOINs when needed.
        3. If the query returns an error, fix the SQL and try again.
        4. Show the SQL you ran, then explain the result, using a SQL analogy when it helps.""",
    tools=[get_weather, get_current_time, get_table_row_count, get_database_schema, run_sql_query],
)

app = App(
    root_agent=root_agent,
    name="app",
)
