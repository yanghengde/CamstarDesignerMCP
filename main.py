"""
Camstar Modeling MCP Agent — 启动入口
=======================================
Usage: python main.py
"""

import sys

# 确保控制台输出 UTF-8
sys.stdout.reconfigure(encoding='utf-8')

import uvicorn
from web.app import create_app

app = create_app()

if __name__ == "__main__":
    print("启动服务器于 http://0.0.0.0:8030")
    uvicorn.run("main:app", host="0.0.0.0", port=8030, reload=False)
