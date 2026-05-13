
import sqlite3
conn = sqlite3.connect('device_mvp_v3.db')
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(work_orders)")
print("Work Orders Columns:", cursor.fetchall())
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='system_logs'")
print("System Logs Table exists:", cursor.fetchone())
conn.close()
