
import sqlite3
import os

DB_PATH = 'device_mvp_v3.db'

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 1. 检查并迁移 inspection_templates
        cursor.execute("PRAGMA table_info(inspection_templates)")
        cols = [c[1] for c in cursor.fetchall()]
        
        if 'period_days' not in cols:
            print("Adding period_days and push_time to inspection_templates...")
            # 由于 SQLite 限制，先添加列
            cursor.execute("ALTER TABLE inspection_templates ADD COLUMN period_days INTEGER DEFAULT 1")
            cursor.execute("ALTER TABLE inspection_templates ADD COLUMN push_time TEXT DEFAULT '08:00'")
            
            # 如果有旧数据，迁移
            if 'period_hours' in cols:
                cursor.execute("UPDATE inspection_templates SET period_days = CAST(period_hours / 24 AS INTEGER)")
                cursor.execute("UPDATE inspection_templates SET period_days = 1 WHERE period_days < 1")
        
        # 2. 检查并迁移 maintenance_plans
        cursor.execute("PRAGMA table_info(maintenance_plans)")
        cols = [c[1] for c in cursor.fetchall()]
        
        if 'period_days' not in cols:
            print("Adding period_days and push_time to maintenance_plans...")
            cursor.execute("ALTER TABLE maintenance_plans ADD COLUMN period_days INTEGER DEFAULT 30")
            cursor.execute("ALTER TABLE maintenance_plans ADD COLUMN push_time TEXT DEFAULT '08:30'")
            
            if 'period_hours' in cols:
                cursor.execute("UPDATE maintenance_plans SET period_days = CAST(period_hours / 24 AS INTEGER)")
                cursor.execute("UPDATE maintenance_plans SET period_days = 1 WHERE period_days < 1")

        conn.commit()
        print("Migration completed successfully.")
    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
    finally:
        conn.close()

if __name__ == '__main__':
    migrate()
