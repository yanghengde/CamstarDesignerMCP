import sys
import os
import pandas as pd

def main():
    file_path = "docs/DemoWorkflowData.xlsx"
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return
        
    print(f"Reading file: {file_path}")
    try:
        xl = pd.ExcelFile(file_path)
        print(f"Sheet names: {xl.sheet_names}")
        
        for name in xl.sheet_names:
            print(f"\n--- Sheet: {name} ---")
            df = pd.read_excel(xl, sheet_name=name)
            print("Columns:")
            print(list(df.columns))
            print("Shape:", df.shape)
            print("First 5 rows:")
            print(df.head(5))
    except Exception as e:
        print(f"Error reading Excel: {e}")

if __name__ == "__main__":
    main()
