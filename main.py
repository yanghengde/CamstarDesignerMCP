"""
Camstar Designer MCP Agent — 启动入口
=======================================
Usage: python main.py
"""

import sys

# 确保控制台输出 UTF-8
sys.stdout.reconfigure(encoding='utf-8')

import uvicorn
from web.app import create_app
from config import SERVER_PORT, SERVER_HOST

app = create_app()

if __name__ == "__main__":
    print(f"启动服务器于 http://{SERVER_HOST}:{SERVER_PORT}")
    uvicorn.run("main:app", host=SERVER_HOST, port=SERVER_PORT, reload=False)
