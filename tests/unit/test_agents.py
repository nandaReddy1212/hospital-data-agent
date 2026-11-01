"""Checks how the agent team is wired together. No LLM calls are made.

Run with:  uv run pytest tests/unit
"""

import re

from google.adk.tools.agent_tool import AgentTool

from app import agent, prompts


def _tool_names(an_agent):
    return {getattr(t, "name", None) or t.__name__ for t in an_agent.tools}


def test_root_routes_to_two_specialists():
    assert agent.root_agent.name == "my_agent"
    assert [a.name for a in agent.root_agent.sub_agents] == ["data_analyst", "sql_tutor"]


def test_root_has_no_data_tools():
    # The coordinator only routes; it must not be able to query data itself.
    assert agent.root_agent.tools == []


def test_reviewer_is_a_tool_of_the_analyst_not_a_sub_agent():
    reviewer_tools = [t for t in agent.data_analyst.tools if isinstance(t, AgentTool)]
    assert [t.agent.name for t in reviewer_tools] == ["sql_reviewer"]
    assert agent.sql_reviewer not in agent.root_agent.sub_agents


def test_only_the_analyst_can_run_queries():
    assert "run_sql_query" in _tool_names(agent.data_analyst)
    assert "run_sql_query" not in _tool_names(agent.sql_tutor)
    assert "run_sql_query" not in _tool_names(agent.sql_reviewer)


def test_every_agent_has_a_description():
    # Descriptions are what the coordinator reads to choose a specialist.
    for a in [agent.data_analyst, agent.sql_tutor, agent.sql_reviewer]:
        assert a.description


def test_prompt_placeholders_are_valid_state_keys():
    # ADK replaces {key} and {key?} with session state. A stray brace in a
    # prompt would break the agent at runtime, so only allow known keys.
    allowed = {"last_query?", "user:answer_format?"}
    for text in [prompts.COORDINATOR_INSTRUCTION, prompts.ANALYST_INSTRUCTION,
                 prompts.REVIEWER_INSTRUCTION, prompts.TUTOR_INSTRUCTION]:
        assert set(re.findall(r"\{([^{}]*)\}", text)) <= allowed