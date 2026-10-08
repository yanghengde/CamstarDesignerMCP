from agent.safety import (
    MutationCounts,
    count_tool_calls,
    evaluate_mutations,
    infer_intended_mutations,
    is_explicit_confirmation,
    is_explicit_rejection,
)
from config import SAFE_CREATE_THRESHOLD, SAFE_DELETE_THRESHOLD


def _call(name: str, arguments: str = "{}") -> dict:
    return {
        "id": "call-1",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def test_confirmation_requires_an_explicit_positive_reply():
    assert is_explicit_confirmation("确认创建") is True
    assert is_explicit_confirmation("继续执行") is True
    assert is_explicit_confirmation("yes") is True

    assert is_explicit_confirmation("不是") is False
    assert is_explicit_confirmation("不要创建") is False
    assert is_explicit_confirmation("我想修改一下参数") is False
    assert is_explicit_confirmation("取消执行") is False


def test_rejection_is_explicit_and_narrow():
    assert is_explicit_rejection("取消执行") is True
    assert is_explicit_rejection("不确认") is True
    assert is_explicit_rejection("稍后再说") is False


def test_batch_count_uses_list_or_count_arguments():
    calls = [
        _call("container_start", '{"container_names":["SN1","SN2","SN3"]}'),
        _call("create_spec", '{"count": 4}'),
        _call("get_spec"),
    ]
    assert count_tool_calls(calls) == MutationCounts(creates=7)


def test_safety_decision_uses_cumulative_counts():
    completed = MutationCounts(creates=SAFE_CREATE_THRESHOLD)
    decision = evaluate_mutations(completed, [_call("container_start")])
    assert decision.requires_approval is True
    assert "创建" in decision.categories


def test_delete_threshold_blocks_as_configured():
    pending = [_call("delete_spec") for _ in range(SAFE_DELETE_THRESHOLD + 1)]
    decision = evaluate_mutations(MutationCounts(), pending)
    assert decision.requires_approval is True
    assert "删除" in decision.categories


def test_explicit_container_count_blocks_before_first_write():
    pending = [_call("container_start")]
    intended = infer_intended_mutations(
        "请生成50个独立的container，每个qty为1", pending
    )
    decision = evaluate_mutations(
        MutationCounts(), pending, intended=intended
    )

    assert intended.creates == 50
    assert decision.requires_approval is True
    assert decision.totals.creates == 50


def test_serial_range_is_counted_as_intended_creations():
    intended = infer_intended_mutations(
        "创建 SN0000001 到 SN0000050", [_call("container_start")]
    )
    assert intended.creates == 50
