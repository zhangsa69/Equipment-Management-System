
import sqlite3
conn = sqlite3.connect('device_mvp_v3.db')
cursor = conn.cursor()
try:
    cursor.execute("ALTER TABLE work_orders ADD COLUMN repair_notes VARCHAR(500)")
    print("Column repair_notes added successfully")
except Exception as e:
    print("Error or already exists:", e)
conn.commit()
conn.close()
