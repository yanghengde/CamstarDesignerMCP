"""
Spec 工具快速冒烟测试
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding='utf-8')

# 确保 config 先加载 (会自动 load_dotenv)
import config  # noqa: F401

from tools.specs import get_specs_count, list_specs


async def main():
    print("=== Test 1: get_specs_count ===")
    try:
        count_result = await get_specs_count()
        print(count_result)
    except Exception as e:
        print(f"Error: {e}")

    print("\n=== Test 2: list_specs (limited output) ===")
    try:
        list_result = await list_specs()
        print(list_result[:1500] if len(list_result) > 1500 else list_result)
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
