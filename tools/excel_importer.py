"""
Excel Importer Tools
====================
Provides tools to parse Excel schemas and import Workflows from Excel files.
"""

import os
import json
import pandas as pd
from typing import Any, Dict, List, Optional
from tools import mcp
from tools.workflows import create_workflow, list_workflows, rebuild_workflow_route


def get_case_insensitive_key(data: Any, key_name: str) -> Optional[Any]:
    """
    Safely extracts a key from JSON string/dict/list case-insensitively,
    supporting OData 'value' arrays and text warning prefixes.
    """
    if isinstance(data, str):
        try:
            # Strip warning message prefix if present
            if "{" in data:
                data = data[data.find("{"):]
            data = json.loads(data)
        except Exception:
            return None
            
    if isinstance(data, dict):
        for k, v in data.items():
            if k.lower() == key_name.lower():
                return v
        # Also check inside "value" if OData wrapped
        if "value" in data and isinstance(data["value"], list) and len(data["value"]) > 0:
            return get_case_insensitive_key(data["value"][0], key_name)
    elif isinstance(data, list) and len(data) > 0:
        return get_case_insensitive_key(data[0], key_name)
        
    return None


@mcp.tool
async def parse_excel_schema(file_path: str) -> str:
    """
    Parse an Excel file's structure (sheets, columns, and sample rows)
    to help mapping columns to MES entity fields.
    
    Required fields:
      - file_path: Absolute or relative path to the Excel file.
    """
    if not os.path.exists(file_path):
        return f"❌ File not found at path: {file_path}"
    
    try:
        xl = pd.ExcelFile(file_path)
        schema_info = {}
        
        for sheet_name in xl.sheet_names:
            df = pd.read_excel(xl, sheet_name=sheet_name, nrows=3)
            df = df.fillna("")
            
            schema_info[sheet_name] = {
                "columns": list(df.columns),
                "row_count_estimate": pd.read_excel(xl, sheet_name=sheet_name).shape[0],
                "sample_rows": df.to_dict(orient="records")
            }
            
        return json.dumps({
            "success": True,
            "file_path": file_path,
            "sheets": schema_info
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        return f"❌ Failed to parse Excel schema: {str(e)}"


@mcp.tool
async def import_workflows_from_excel(file_path: str, mapping_json: str) -> str:
    """
    Import Workflows from an Excel sheet based on a mapping configuration.
    
    Required fields:
      - file_path: Absolute or relative path to the Excel file.
      - mapping_json: JSON string mapping Excel columns to Workflow fields.
        Example mapping:
        {
          "sheet_name": "Workflows",
          "fields": {
            "workflow_name": "工艺名称",
            "workflow_revision": "版本",
            "workflow_description": "描述",
            "step_name": "工步名称",
            "spec_name": "规格名称",
            "spec_revision": "规格版本",
            "sequence": "序号"
          }
        }
    """
    if not os.path.exists(file_path):
        return f"❌ File not found at path: {file_path}"
        
    try:
        mapping = json.loads(mapping_json)
    except json.JSONDecodeError as e:
        return f"❌ Invalid mapping_json: {str(e)}"
        
    sheet_name = mapping.get("sheet_name")
    fields = mapping.get("fields", {})
    
    if not sheet_name:
        return "❌ mapping_json must specify 'sheet_name'"
        
    req_fields = ["workflow_name", "workflow_revision", "step_name", "spec_name"]
    missing_fields = [f for f in req_fields if f not in fields]
    if missing_fields:
        return f"❌ mapping_json fields is missing required mappings: {missing_fields}"
        
    try:
        df = pd.read_excel(file_path, sheet_name=sheet_name)
    except Exception as e:
        return f"❌ Failed to read sheet '{sheet_name}' in file '{file_path}': {str(e)}"
        
    # Check if mapped columns exist
    excel_cols = df.columns
    for key, col_name in fields.items():
        if col_name not in excel_cols:
            return f"❌ Column '{col_name}' mapped to '{key}' does not exist in the Excel sheet."
            
    # Clean NaN values
    df = df.fillna("")
    
    # Extract mappings
    col_wf_name = fields["workflow_name"]
    col_wf_rev = fields["workflow_revision"]
    col_wf_desc = fields.get("workflow_description")
    col_step_name = fields["step_name"]
    col_spec_name = fields["spec_name"]
    col_spec_rev = fields.get("spec_revision")
    col_seq = fields.get("sequence")
    
    # Group by Workflow Name and Revision
    grouped = df.groupby([col_wf_name, col_wf_rev])
    
    import_results = []
    
    for (wf_name, wf_rev), group in grouped:
        wf_name = str(wf_name).strip()
        wf_rev = str(wf_rev).strip()
        
        if not wf_name or not wf_rev:
            continue
            
        import_results.append(f"====== Importing Workflow '{wf_name}' (Rev: '{wf_rev}') ======")
        
        # Get description
        wf_desc = ""
        if col_wf_desc:
            first_row_desc = group.iloc[0][col_wf_desc]
            wf_desc = str(first_row_desc).strip()
            
        # Compile steps
        steps_list = []
        for idx, row in group.iterrows():
            step_name = str(row[col_step_name]).strip()
            spec_name = str(row[col_spec_name]).strip()
            
            if not step_name or not spec_name:
                continue
                
            spec_rev = None
            if col_spec_rev and row[col_spec_rev]:
                spec_rev = str(row[col_spec_rev]).strip()
                
            sequence = None
            if col_seq:
                try:
                    sequence = int(float(row[col_seq]))
                except ValueError:
                    pass
                    
            steps_list.append({
                "step_name": step_name,
                "spec_name": spec_name,
                "spec_revision": spec_rev,
                "sequence": sequence
            })
            
        if any(s["sequence"] is not None for s in steps_list):
            steps_list.sort(key=lambda x: (x["sequence"] is None, x["sequence"]))
            
        # Try to resolve workflow Instance ID
        wf_instance_id = None
        try:
            # Check if workflow already exists in Camstar and get its InstanceId
            check_res = await list_workflows(filter_expr=f"name eq '{wf_name}' and revision eq '{wf_rev}'")
            wf_instance_id = get_case_insensitive_key(check_res, "instanceid")
        except Exception as e:
            import_results.append(f"Error checking existing workflow: {e}")
            
        if wf_instance_id:
            import_results.append(f"ℹ️ Workflow '{wf_name}:{wf_rev}' already exists (InstanceId: {wf_instance_id}).")
        else:
            import_results.append(f"Creating new Workflow '{wf_name}:{wf_rev}'...")
            create_res = await create_workflow(name=wf_name, revision=wf_rev, description=wf_desc)
            if "error" in create_res.lower() or "failed" in create_res.lower():
                import_results.append(f"❌ Failed to create Workflow: {create_res}")
                continue
                
            wf_instance_id = get_case_insensitive_key(create_res, "instanceid")
            if not wf_instance_id:
                import_results.append(f"❌ Failed to extract InstanceId from creation response: {create_res}")
                continue
            import_results.append(f"✅ Workflow created successfully (InstanceId: {wf_instance_id}).")
            
        # Rebuild route using the resolved Instance ID
        import_results.append(f"Rebuilding steps for workflow (InstanceId: {wf_instance_id})...")
        
        route_json_input = []
        for s in steps_list:
            route_json_input.append({
                "step_name": s["step_name"],
                "spec_name": s["spec_name"],
                "spec_revision": s["spec_revision"]
            })
            
        rebuild_res = await rebuild_workflow_route(
            workflow_id=wf_instance_id,
            workflow_name=wf_name,
            workflow_revision=wf_rev,
            route_json=json.dumps(route_json_input)
        )
        
        import_results.append(rebuild_res)
        import_results.append(f"================================================\n")
        
    return "\n".join(import_results)
