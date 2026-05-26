from datetime import datetime, timedelta
from main import SessionLocal, WorkOrder, User, Device, check_uncompleted_work_orders_and_notify

def main():
    print("[TEST] Starting test for overdue work orders...")
    db = SessionLocal()
    try:
        # 1. Ensure users exist
        for name, uid in [("张洒", "2636464912782834"), ("李宝军", "21271149341291549353")]:
            u = db.query(User).filter(User.id == uid).first()
            if not u:
                db.add(User(id=uid, name=name, job_title="测试高管"))
                print(f"[TEST] Added fallback user: {name} ({uid})")
        db.commit()

        # 2. Find a device
        dev = db.query(Device).first()
        if not dev:
            print("[TEST] No devices found to attach a test work order to.")
            return

        # 3. Create a test work order created 3 days ago (overdue)
        three_days_ago = datetime.now() - timedelta(days=3)
        test_order_id = "test-overdue-order-123"
        
        # Clean up existing test order if present
        from main import WorkOrderNotificationLog
        db.query(WorkOrder).filter(WorkOrder.id == test_order_id).delete()
        db.query(WorkOrderNotificationLog).filter(WorkOrderNotificationLog.work_order_id == test_order_id).delete()
        db.commit()

        test_order = WorkOrder(
            id=test_order_id,
            device_id=dev.id,
            reporter_id="2636464912782834", # 张洒
            leader_id="21271149341291549353", # 李宝军
            description="[自动测试] 这是一条模拟3天前创建且仍未完工的超时待处理测试工单",
            status="待处理",
            created_at=three_days_ago
        )
        db.add(test_order)
        db.commit()
        print(f"[TEST] Created test work order: {test_order_id} (Created 3 days ago, Status: 待处理)")

        # 4. Trigger check_uncompleted_work_orders_and_notify
        print("[TEST] Executing check_uncompleted_work_orders_and_notify()...")
        check_uncompleted_work_orders_and_notify()
        print("[TEST] Finished test execution.")

        # Clean up test order
        db.query(WorkOrder).filter(WorkOrder.id == test_order_id).delete()
        db.query(WorkOrderNotificationLog).filter(WorkOrderNotificationLog.work_order_id == test_order_id).delete()
        db.commit()
        print("[TEST] Cleaned up test data.")

    finally:
        db.close()

if __name__ == "__main__":
    main()
