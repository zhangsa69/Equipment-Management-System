import sqlite3

def upgrade():
    conn = sqlite3.connect('e:/设备管理/device_mvp_v3.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute("ALTER TABLE devices ADD COLUMN maintenance_leader_id VARCHAR(50)")
        print("Column maintenance_leader_id added.")
    except sqlite3.OperationalError:
        print("Column maintenance_leader_id already exists.")

    try:
        cursor.execute("ALTER TABLE devices ADD COLUMN last_maintenance_notified_time DATETIME")
        print("Column last_maintenance_notified_time added.")
    except sqlite3.OperationalError:
        print("Column last_maintenance_notified_time already exists.")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    upgrade()
