"""
FastAPI 应用工厂
==================
创建并配置 FastAPI 应用实例。
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from config import (
    ENABLE_MCP_HTTP,
    MCP_ALLOWED_HOSTS,
    MCP_ALLOWED_ORIGINS,
    MCP_API_KEY,
    MCP_HTTP_PATH,
)
from agent.memory import init_memory
from agent.llm_client import oai_client, openai_tools, register_tools
from tools import mcp
from web.routes import router
from web.mcp_transport import BearerTokenMiddleware
from agent.langgraph_runtime import init_langgraph_runtime, close_langgraph_runtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时加载记忆和注册工具。"""
    # 恢复历史记忆
    init_memory()

    # 注册 MCP 工具
    await register_tools()

    await init_langgraph_runtime(oai_client, openai_tools)

    print("[READY] Web 服务启动就绪！")
    mcp_http_app = getattr(app.state, "mcp_http_app", None)
    try:
        # Mounted Starlette applications do not run their lifespan implicitly.
        # FastMCP's session/task manager must therefore share the parent lifespan.
        if mcp_http_app is not None:
            async with mcp_http_app.router.lifespan_context(mcp_http_app):
                yield
        else:
            yield
    finally:
        await close_langgraph_runtime()



def create_app() -> FastAPI:
    """创建并返回配置完毕的 FastAPI 实例。"""
    app = FastAPI(
        title="Siemens Opcenter Designer AI Agent",
        lifespan=lifespan
    )

    # 挂载静态资源
    svg_dir = os.path.join("static", "svg")
    if os.path.isdir(svg_dir):
        app.mount("/svg", StaticFiles(directory=svg_dir), name="svg")

    # 注册路由
    app.include_router(router)

    if ENABLE_MCP_HTTP:
        if not MCP_API_KEY:
            raise RuntimeError(
                "ENABLE_MCP_HTTP=True requires MCP_API_KEY. "
                "Refusing to expose Designer metadata tools without authentication."
            )

        mcp_http_app = mcp.http_app(
            path="/",
            transport="streamable-http",
            stateless_http=True,
            json_response=True,
            allowed_hosts=MCP_ALLOWED_HOSTS,
            allowed_origins=MCP_ALLOWED_ORIGINS or None,
        )
        app.state.mcp_http_app = mcp_http_app
        app.mount(
            MCP_HTTP_PATH,
            BearerTokenMiddleware(mcp_http_app, MCP_API_KEY),
            name="mcp",
        )
        print(f"[MCP] Streamable HTTP endpoint: {MCP_HTTP_PATH}/")
    else:
        app.state.mcp_http_app = None
        print("[MCP] 外部 Streamable HTTP 未启用（ENABLE_MCP_HTTP=False）。")

    return app
