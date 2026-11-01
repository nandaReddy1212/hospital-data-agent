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
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from .prompts import (
    ANALYST_INSTRUCTION,
    COORDINATOR_INSTRUCTION,
    REVIEWER_INSTRUCTION,
    TUTOR_INSTRUCTION,
)
from .tools import (
    explain_query,
    get_database_schema,
    get_query_history,
    run_sql_query,
    set_answer_format,
)

MODEL = "gemini-3.8-flash"


def gemini() -> Gemini:
    """One Gemini model config shared by every agent, with retries on 429/503."""
    return Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=5, initial_delay=2, exp_base=2),
        api_key=os.environ.get("GEMINI_API_KEY"),
    )


# ---------------------------------------------------------------------------
# Agent team
#
#                    my_agent (coordinator)
#                    /                     \
#          data_analyst                   sql_tutor
#               |
#      sql_reviewer (used as a tool)
#
# Two multi-agent patterns are used:
#   1. sub_agents  -> the coordinator TRANSFERS the conversation to a
#      specialist, which then talks to the user directly.
#   2. AgentTool   -> the analyst CALLS the reviewer like a function and gets
#      its reply back; the reviewer never talks to the user.
# ---------------------------------------------------------------------------

sql_reviewer = Agent(
    name="sql_reviewer",
    model=gemini(),
    description="Reviews a SQL query against the user's question and the schema. "
                "Send it the question and the full SQL; it replies APPROVED or REVISE.",
    instruction=REVIEWER_INSTRUCTION,
    tools=[get_database_schema],
)

data_analyst = Agent(
    name="data_analyst",
    model=gemini(),
    description="Answers questions that need data from the hospital database by "
                "writing, reviewing and running SQL. Also handles follow-up questions.",
    instruction=ANALYST_INSTRUCTION,
    tools=[
        get_database_schema,
        run_sql_query,
        explain_query,
        get_query_history,
        set_answer_format,
        AgentTool(agent=sql_reviewer),
    ],
)

sql_tutor = Agent(
    name="sql_tutor",
    model=gemini(),
    description="Explains SQL and data engineering concepts with examples on the "
                "hospital tables. Does not fetch data answers.",
    instruction=TUTOR_INSTRUCTION,
    tools=[get_database_schema, explain_query],
)

root_agent = Agent(
    # Keep in sync with agents-cli-manifest.yaml: agents-cli derives this name
    # from the project `name:` recorded there, and telemetry reports it as
    # gen_ai.agent.name. Renaming the agent only here makes the two disagree,
    # and anything selecting traces by name stops finding this agent's.
    name="my_agent",
    model=gemini(),
    description="Coordinator that routes each message to the right specialist.",
    instruction=COORDINATOR_INSTRUCTION,
    sub_agents=[data_analyst, sql_tutor],
)

app = App(
    root_agent=root_agent,
    name="app",
)