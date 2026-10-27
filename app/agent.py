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

import os

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from .tools import explain_query, get_database_schema, run_sql_query

MODEL = "gemini-3.8-flash"

INSTRUCTION = """You are a hospital data assistant and data engineering tutor.
You answer questions about a SYNTHETIC hospital SQLite database with the tables
departments, doctors, patients and visits.

How to answer a data question:
1. Call get_database_schema to learn the exact columns, join keys and allowed values.
   You only need to do this once per conversation.
2. Write ONE SQLite SELECT query and call run_sql_query.
   - Join tables using the keys listed under "joins" in the schema.
   - Use the exact spelling and case shown in "allowed_values" when filtering.
   - Prefer COUNT, SUM, AVG and GROUP BY over returning raw rows.
3. If the tool returns status "error", read the "hint", fix the SQL and try again.
   Try at most 3 times, then explain what went wrong.
4. Reply with the SQL you ran in a ```sql block, then the answer in plain language.
   When it helps, explain the SQL concept used (JOIN, GROUP BY, window function...).

If the user asks how a query works or why it might be slow, call explain_query.

Rules:
- Never invent numbers. Every figure must come from a tool result.
- You can only read data. If asked to change, add or delete data, say that this
  assistant is read-only.
- If a question cannot be answered from these tables, say so and suggest what
  data would be needed.
"""

root_agent = Agent(
    # Keep in sync with agents-cli-manifest.yaml: agents-cli derives this name
    # from the project `name:` recorded there, and telemetry reports it as
    # gen_ai.agent.name. Renaming the agent only here makes the two disagree,
    # and anything selecting traces by name stops finding this agent's.
    name="my_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=5,
                                             initial_delay=2,
                                             exp_base=2),
        api_key=os.environ.get("GEMINI_API_KEY"),
    ),
    instruction=INSTRUCTION,
    tools=[get_database_schema, run_sql_query, explain_query],
)

app = App(
    root_agent=root_agent,
    name="app",
)