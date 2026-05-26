from datetime import datetime, timedelta
from main import SessionLocal, WorkOrder, check_uncompleted_work_orders_and_notify, WorkOrderNotificationLog

def test_user_work_order():
    print("[TEST] Starting simulation using user's recently submitted work order...")
    db = SessionLocal()
    order_id = "1969b3b6-bbce-4f85-85f4-6797fc7235db"
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        print(f"[TEST] Error: Work order {order_id} not found in DB.")
        return
        
    original_created_at = order.created_at
    print(f"[TEST] Found user's work order: {order_id}")
    print(f"[TEST] Original created_at: {original_created_at}")
    print(f"[TEST] Status: {order.status}")
    
    try:
        # 1. Backdate the created_at to 3 days ago
        order.created_at = datetime.now() - timedelta(days=3)
        db.commit()
        print("[TEST] Temporarily backdated work order created_at to 3 days ago.")
        
        # 2. Clear any existing notification log for this work order to bypass throttle
        db.query(WorkOrderNotificationLog).filter(WorkOrderNotificationLog.work_order_id == order_id).delete()
        db.commit()
        print("[TEST] Cleared notification log for this work order.")
        
        # 3. Call the actual system notify task
        print("[TEST] Invoking check_uncompleted_work_orders_and_notify()...")
        check_uncompleted_work_orders_and_notify()
        print("[TEST] Invocation completed.")
        
    finally:
        # 4. Restore original created_at
        order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
        order.created_at = original_created_at
        db.commit()
        print("[TEST] Restored original created_at for the work order.")
        
        # 5. Clean up the notification log entry so it can be notified naturally in the future
        db.query(WorkOrderNotificationLog).filter(WorkOrderNotificationLog.work_order_id == order_id).delete()
        db.commit()
        print("[TEST] Cleaned up notification log entry to preserve future natural notifications.")
        db.close()

if __name__ == "__main__":
    test_user_work_order()
