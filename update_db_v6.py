import sqlite3
import json

db_path = "device_mvp_v3.db"

def upgrade_db():
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Step 1: Create the new structure for maintenance_plans
    cursor.execute("""
    CREATE TABLE maintenance_plans_new (
        id INTEGER NOT NULL, 
        name VARCHAR(100) NOT NULL, 
        items JSON NOT NULL, 
        period_months INTEGER, 
        push_day INTEGER, 
        push_time VARCHAR(10), 
        PRIMARY KEY (id)
    );
    """)

    # Step 2: Extract old data and map it to new schema
    cursor.execute("SELECT id, name, items, period_days, push_time FROM maintenance_plans")
    rows = cursor.fetchall()

    for row in rows:
        plan_id, name, items, period_days, push_time = row
        # Map period_days linearly back to period_months (e.g. 30->1, 90->3)
        period_months = max(1, min(12, int(period_days / 30))) if period_days else 1
        push_day = 28 # Default to a safe limit

        cursor.execute(
            """
            INSERT INTO maintenance_plans_new (id, name, items, period_months, push_day, push_time)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (plan_id, name, items, period_months, push_day, push_time)
        )

    # Step 3: Swap the tables
    cursor.execute("DROP TABLE maintenance_plans")
    cursor.execute("ALTER TABLE maintenance_plans_new RENAME TO maintenance_plans")

    conn.commit()
    conn.close()
    print("Database updated to v6 structure successfully.")

if __name__ == "__main__":
    upgrade_db()
