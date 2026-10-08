"""Tests for coordination between agent-loop and graph-step limits."""

from agent import langgraph_runtime as runtime


def test_graph_config_includes_explicit_recursion_limit():
    config = runtime._graph_config("operator", "session-1")

    assert config["configurable"]["thread_id"] == "operator:session-1"
    assert config["recursion_limit"] == runtime.LANGGRAPH_RECURSION_LIMIT
    assert config["recursion_limit"] >= 25
