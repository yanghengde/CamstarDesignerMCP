"""
MfgOrder and MfgLine MCP Tools Smoke Test
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding='utf-8')

# Ensure config is loaded
import config  # noqa: F401

from tools.mfglines import get_mfglines_count, list_mfglines
from tools.mfgorders import get_mfgorders_count, list_mfgorders
from tools.producttypes import get_producttypes_count, list_producttypes


async def main():
    print("=== Test 1: get_mfglines_count ===")
    try:
        count_result = await get_mfglines_count()
        print(count_result)
    except Exception as e:
        print(f"Error: {e}")

    print("\n=== Test 2: list_mfglines (limited output) ===")
    try:
        list_result = await list_mfglines(top=5)
        print(list_result[:1000] if len(list_result) > 1000 else list_result)
    except Exception as e:
        print(f"Error: {e}")

    print("\n=== Test 3: get_mfgorders_count ===")
    try:
        count_result = await get_mfgorders_count()
        print(count_result)
    except Exception as e:
        print(f"Error: {e}")

    print("\n=== Test 4: list_mfgorders (limited output) ===")
    try:
        list_result = await list_mfgorders(top=5)
        print(list_result[:1000] if len(list_result) > 1000 else list_result)
    except Exception as e:
        print(f"Error: {e}")

    print("\n=== Test 5: get_producttypes_count ===")
    try:
        count_result = await get_producttypes_count()
        print(count_result)
    except Exception as e:
        print(f"Error: {e}")

    print("\n=== Test 6: list_producttypes (limited output) ===")
    try:
        list_result = await list_producttypes(top=5)
        print(list_result[:1000] if len(list_result) > 1000 else list_result)
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
