import os
import sys
sys.path.append(os.getcwd())
import asyncio
import pandas as pd
from dotenv import load_dotenv

# Load environmental variables
load_dotenv()

from core.http_client import request, close_client
from tools.specs import list_specs

async def main():
    print("Connecting to Camstar to find existing Specs...")
    try:
        # Retrieve list of specs
        specs_raw = await list_specs(top=5)
        
        # Strip warning prefix if present
        if "{" in specs_raw:
            specs_raw_json = specs_raw[specs_raw.find("{"):]
        else:
            specs_raw_json = specs_raw

        import json
        data_json = json.loads(specs_raw_json)
        
        specs_list = []
        if isinstance(data_json, dict):
            specs_list = data_json.get("value", [])
        elif isinstance(data_json, list):
            specs_list = data_json
        
        spec_names = []
        if isinstance(specs_list, list) and len(specs_list) > 0:
            for spec in specs_list:
                name = spec.get("Name")
                if name:
                    spec_names.append(name)
        
        # Fallback if no specs are returned
        if not spec_names:
            print("No specs found in Camstar. We will use fallback spec name 'HD-001'.")
            spec_names = ["HD-001"]
        else:
            print(f"Found existing specs in Camstar: {spec_names}")
            
        # Create a mock Excel sheet for testing workflow import
        # We will create 1 workflow 'WF-TEST-EXCEL' revision '1' with 3 steps
        data = {
            "工艺名称": ["WF-TEST-EXCEL", "WF-TEST-EXCEL", "WF-TEST-EXCEL"],
            "版本": ["1", "1", "1"],
            "描述": ["测试Excel导入工艺", "测试Excel导入工艺", "测试Excel导入工艺"],
            "工卡名称": ["Step-Start", "Step-Process", "Step-Finish"],
            "规格名称": [spec_names[0], spec_names[0], spec_names[0]], # Reuse first spec
            "序号": [10, 20, 30]
        }
        
        # If we have multiple specs, use them
        if len(spec_names) >= 2:
            data["规格名称"][1] = spec_names[1]
        if len(spec_names) >= 3:
            data["规格名称"][2] = spec_names[2]
            
        df = pd.DataFrame(data)
        os.makedirs("scratch", exist_ok=True)
        excel_path = "scratch/test_workflow.xlsx"
        df.to_excel(excel_path, index=False, sheet_name="Workflows")
        print(f"Success: Wrote mock Excel file to '{excel_path}'")
        
    except Exception as e:
        print(f"Error occurred: {str(e).encode('utf-8', errors='ignore').decode('gbk', errors='ignore')}")
    finally:
        await close_client()

if __name__ == "__main__":
    asyncio.run(main())
