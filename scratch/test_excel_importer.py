import os
import sys
sys.path.append(os.getcwd())
import asyncio
import json
from dotenv import load_dotenv

# Load environmental variables
load_dotenv()

from core.http_client import close_client
from tools.excel_importer import parse_excel_schema, import_workflows_from_excel

def safe_print(text):
    """Safely print text on Windows terminal (GBK or UTF-8)."""
    try:
        print(text)
    except UnicodeEncodeError:
        try:
            print(text.encode(sys.stdout.encoding or 'gbk', errors='replace').decode(sys.stdout.encoding or 'gbk'))
        except Exception:
            # Fallback to pure ascii or raw print if it still fails
            print(text.encode('ascii', errors='replace').decode('ascii'))

async def main():
    file_path = "scratch/test_workflow.xlsx"
    safe_print(f"=== Testing parse_excel_schema for {file_path} ===")
    schema_res = await parse_excel_schema(file_path)
    safe_print(schema_res)
    
    safe_print("\n=== Testing import_workflows_from_excel ===")
    mapping = {
        "sheet_name": "Workflows",
        "fields": {
            "workflow_name": "工艺名称",
            "workflow_revision": "版本",
            "workflow_description": "描述",
            "step_name": "工卡名称",
            "spec_name": "规格名称",
            "sequence": "序号"
        }
    }
    mapping_json = json.dumps(mapping)
    import_res = await import_workflows_from_excel(file_path, mapping_json)
    safe_print(import_res)
    
    await close_client()

if __name__ == "__main__":
    asyncio.run(main())
