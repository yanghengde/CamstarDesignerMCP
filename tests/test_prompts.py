"""Prompt policy regression tests."""

from agent.prompts import SYSTEM_PROMPT, USER_FACING_LANGUAGE_RULE


def test_system_prompt_requires_chinese_for_all_user_facing_progress():
    assert "所有面向用户展示的内容必须使用简体中文" in USER_FACING_LANGUAGE_RULE
    assert "工具调用前的计划" in USER_FACING_LANGUAGE_RULE
    assert "英文过程提示" in USER_FACING_LANGUAGE_RULE
    assert USER_FACING_LANGUAGE_RULE in SYSTEM_PROMPT
