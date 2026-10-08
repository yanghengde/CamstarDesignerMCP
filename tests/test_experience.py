import pytest

from agent import experience


@pytest.fixture
def experience_db(tmp_path, monkeypatch):
    path = tmp_path / "experience.sqlite"
    monkeypatch.setattr(experience, "EXPERIENCE_DB", str(path))
    return path


def test_failure_candidates_are_sanitized_and_deduplicated(experience_db):
    first_id = experience.record_tool_outcome(
        session_id="s1",
        tool_name="container_start",
        arguments={"container_name": "SN0000001", "password": "secret"},
        result=(
            "❌ HTTP 400 Error token=secret-value "
            "https://mes.local/api/Start requires input 123456"
        ),
    )
    second_id = experience.record_tool_outcome(
        session_id="s2",
        tool_name="container_start",
        arguments={"container_name": "SN0000002"},
        result=(
            "❌ HTTP 400 Error token=another-secret "
            "https://other.local/api/Start requires input 987654"
        ),
    )

    assert first_id == second_id
    candidates = experience.list_candidates("pending")
    assert len(candidates) == 1
    assert candidates[0]["occurrences"] == 2
    assert "secret-value" not in candidates[0]["symptom"]
    assert "https://" not in candidates[0]["symptom"]


def test_success_is_an_event_but_not_a_candidate(experience_db):
    candidate_id = experience.record_tool_outcome(
        session_id="s1",
        tool_name="get_spec",
        arguments={"name": "S1"},
        result="ok",
    )

    assert candidate_id is None
    assert experience.get_experience_status() == {
        "events": 1,
        "pending": 0,
        "approved": 0,
        "rejected": 0,
    }


def test_only_human_approved_resolution_is_injected(experience_db):
    candidate_id = experience.record_tool_outcome(
        session_id="s1",
        tool_name="container_start",
        arguments={},
        result="Error: Field Owner requires input",
    )
    pending_context = experience.build_runtime_experience_context(
        "unrelated follow-up", {"container_start"}
    )
    assert "Human-approved operational experience" not in pending_context

    experience.review_candidate(
        candidate_id,
        status="approved",
        resolution="Resolve Details.Owner with RequestSelectionValues",
        evidence="Verified in regression test",
    )
    approved_context = experience.build_runtime_experience_context(
        "unrelated follow-up", {"container_start"}
    )
    assert "Human-approved operational experience" in approved_context
    assert "Resolve Details.Owner" in approved_context


def test_approval_requires_a_resolution(experience_db):
    candidate_id = experience.record_tool_outcome(
        session_id="s1",
        tool_name="container_start",
        arguments={},
        result="Error: unknown field",
    )
    with pytest.raises(ValueError, match="resolution is required"):
        experience.review_candidate(candidate_id, status="approved")


def test_container_start_skill_is_loaded_only_for_relevant_request(experience_db):
    relevant = experience.build_runtime_experience_context("请生成50个序列号")
    unrelated = experience.build_runtime_experience_context("查询所有 Spec")

    assert "Camstar Start Contract" in relevant
    assert unrelated == ""
