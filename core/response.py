"""
智能响应裁剪
=============
当 API 返回的 JSON 过长时，自动提取关键字段以免超出 LLM 上下文窗口。
"""

import json
from config import MAX_RESPONSE_LENGTH

# Fields we consider "key" (lowercase for case-insensitive match)
KEY_FIELDS = {
    "instanceid",
    "displayname",
    "name",
    "revision",
    "status",
    "description",
    "isfrozen",
    "isrevofrcd",
    "lastchangedate",
    "lastchangedategmt",
    "creationdate",
    "creationdategmt",
    "creationusername",
    "currentstatus",
    "control",
    "eco",
    "operation",
    "useror",
}


def extract_key_fields(obj):
    """
    Recursively extract only KEY_FIELDS from an object or list.
    Supports case-insensitive key matching and OData 'value' arrays.
    """
    if isinstance(obj, list):
        return [extract_key_fields(item) for item in obj]

    if isinstance(obj, dict):
        trimmed = {}
        for key, value in obj.items():
            lkey = key.lower()
            if lkey in KEY_FIELDS:
                trimmed[key] = value
            elif lkey == "value" and isinstance(value, list):
                trimmed[key] = [extract_key_fields(item) for item in value]

        # Always keep 'operation' sub-object if present (it's a ref)
        for k, v in obj.items():
            if k.lower() == "operation" and isinstance(v, dict):
                trimmed[k] = v
        return trimmed

    return obj


def smart_response(data) -> str:
    """
    Return the JSON string of *data*.
    If it exceeds MAX_RESPONSE_LENGTH, re-serialize with only key fields
    and attach a note that the response was truncated.
    """
    full_text = json.dumps(data, ensure_ascii=False, indent=2)

    if len(full_text) <= MAX_RESPONSE_LENGTH:
        return full_text

    trimmed = extract_key_fields(data)
    trimmed_text = json.dumps(trimmed, ensure_ascii=False, indent=2)
    return (
        "⚠️ Response was too large and has been trimmed to key fields "
        "(instanceID, name, revision, status, description, timestamps, etc.).\n"
        "Use get_spec(key) for the full object.\n\n"
        + trimmed_text
    )


def smart_query_response(data) -> str:
    """
    Serialize Query API results while preserving arbitrary result columns.

    Modeling responses have known key fields, but Query rows can use any
    configured column names. For large Query results, retain complete leading
    rows instead of applying the Modeling key-field filter.
    """
    full_text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(full_text) <= MAX_RESPONSE_LENGTH:
        return full_text

    for row_limit in (20, 10, 5, 2, 1):
        preview = _limit_query_rows(data, row_limit)
        preview_text = json.dumps(preview, ensure_ascii=False, indent=2)
        if len(preview_text) <= MAX_RESPONSE_LENGTH:
            return (
                "⚠️ Query response was too large. The result below preserves "
                f"up to {row_limit} leading row(s) from each result list. "
                "Narrow the Query or its selected fields for complete data.\n\n"
                + preview_text
            )

    return (
        "⚠️ Query response was too large even for a one-row preview. Narrow "
        "the Query or its selected fields. The text below is truncated.\n\n"
        + full_text[:MAX_RESPONSE_LENGTH]
    )


def _limit_query_rows(value, row_limit: int):
    """Recursively limit lists while preserving every field in kept rows."""
    if isinstance(value, list):
        return [
            _limit_query_rows(item, row_limit)
            for item in value[:row_limit]
        ]
    if isinstance(value, dict):
        return {
            key: _limit_query_rows(child, row_limit)
            for key, child in value.items()
        }
    return value
