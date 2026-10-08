"""
CLI 交互式 Agent
==================
命令行版本的 Camstar Designer 助手，用于终端调试。
Usage: python -m agent.cli
"""

import asyncio
import sys
import json

sys.stdout.reconfigure(encoding='utf-8')

import config  # noqa: F401  确保 .env 加载
from openai import AsyncOpenAI
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from agent.prompts import SYSTEM_PROMPT
from agent.llm_client import register_tools, openai_tools
from tools import get_tool_func


async def main():
    print("[START] 正在初始化 Camstar Designer MCP Agent (CLI)...")

    client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    # 注册 MCP 工具
    await register_tools()

    print(f"\n[OK] 成功加载了 {len(openai_tools)} 个 Designer 工具 (MCP Tools)。\n")
    print("=" * 60)
    print("欢迎使用 Camstar Designer 助手！你可以问我类似于：")
    print(" - '检查 Designer 环境并列出元数据文件'")
    print(" - '查询 InSite.mdb 中 Container 的字段和类型'")
    print(" - '读取设计 XML，基于已有字段模板生成新增字段草案'")
    print("输入 'q' 或 'exit' 退出。")
    print("=" * 60)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]

    while True:
        try:
            user_input = input("\n🧑 你: ")
        except (KeyboardInterrupt, EOFError):
            break

        if user_input.strip().lower() in ['q', 'exit', 'quit']:
            break

        if not user_input.strip():
            continue

        messages.append({"role": "user", "content": user_input})

        # 循环，直到大模型不再调用工具为止
        while True:
            try:
                response = await client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=messages,
                    tools=openai_tools,
                    tool_choice="auto"
                )
            except Exception as e:
                print(f"[ERROR] 请求 LLM 失败: {e}")
                break

            response_message = response.choices[0].message
            messages.append(response_message)

            if not response_message.tool_calls:
                print(f"\n助手: {response_message.content}")
                break

            for tool_call in response_message.tool_calls:
                func_name = tool_call.function.name
                func_args_str = tool_call.function.arguments

                print(f"\n[RUN] 正在执行操作: {func_name}({func_args_str}) ...")

                try:
                    func_args = json.loads(func_args_str)
                except json.JSONDecodeError:
                    func_args = {}

                try:
                    tool_func = get_tool_func(func_name)
                    if tool_func is None:
                        result = f"Error: tool '{func_name}' not found"
                    else:
                        result = await tool_func(**func_args)
                except Exception as e:
                    result = f"Error executing {func_name}: {e}"

                print("[OK] 执行完毕。正在分析结果...\n")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": func_name,
                    "content": str(result)
                })


if __name__ == "__main__":
    asyncio.run(main())
