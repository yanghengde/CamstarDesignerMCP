"""Run the synthetic offline package workflow without LLM/MDB/Siemens tools."""

import asyncio
from hashlib import sha256
import json
from pathlib import Path

from designer.files import artifact_dir
from tools.designer import (
    check_designer_package, generate_designer_field_package, list_designer_cdos,
)


async def main():
    data = (Path(__file__).resolve().parents[1] / "examples/designer/demo_metadata.xml").read_bytes()
    source = artifact_dir() / "demo_metadata.xml"
    source.write_bytes(data)
    await list_designer_cdos(str(source))
    result = await generate_designer_field_package(
        str(source), sha256(data).hexdigest(), "DemoContainer", "DemoContainer",
        "ExistingText", "ExternalLotNumber", "demo_customer", "外部批次号（离线演示）",
    )
    result["integrity"] = await check_designer_package(result["files"]["manifest.json"])
    result["notice"] = "使用合成样例，仅验证离线流程；没有真实 Designer 导入或发布。"
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
