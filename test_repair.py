
import requests
import uuid

# Simulate a repair submission
API_BASE_URL = "http://127.0.0.1:8000"

# Find a valid device ID and user ID
import sqlite3
conn = sqlite3.connect('device_mvp_v3.db')
c = conn.cursor()
c.execute("SELECT id FROM devices LIMIT 1")
device_id = c.fetchone()[0]
c.execute("SELECT id FROM users LIMIT 1")
user_id = c.fetchone()[0]
conn.close()

payload = {
    "device_id": device_id,
    "reporter_id": user_id,
    "leader_id": user_id,
    "description": "Diagnostic test repair"
}

res = requests.post(f"{API_BASE_URL}/repair/", json=payload)
print(f"Status: {res.status_code}")
if not res.ok:
    print(f"Error detail: {res.text}")
else:
    print("Success")
