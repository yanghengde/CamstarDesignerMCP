"""Compatibility smoke test for the durable LangGraph SQLite checkpointer."""

import asyncio

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


def test_async_sqlite_checkpointer_can_initialize_and_read(tmp_path):
    async def scenario() -> None:
        manager = AsyncSqliteSaver.from_conn_string(
            str(tmp_path / "checkpoint.sqlite")
        )
        saver = await manager.__aenter__()
        try:
            await saver.setup()
            result = await saver.aget_tuple(
                {"configurable": {"thread_id": "compatibility-test"}}
            )
            assert result is None
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())
