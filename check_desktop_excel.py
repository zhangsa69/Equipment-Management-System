import pandas as pd
import os

file_path = r"C:\Users\zhangsa\Desktop\各车间工艺设备清单.xlsx"
if os.path.exists(file_path):
    df = pd.read_excel(file_path, sheet_name=0)
    print(df.columns.tolist())
    print(df.head(2))
else:
    print(f"File not found: {file_path}")
