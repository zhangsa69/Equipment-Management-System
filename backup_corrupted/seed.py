from fastapi.testclient import TestClient
from main import app, Base, engine

# 使用 with 块触发 startup 事件以写入预设数据
with TestClient(app) as client:
    print("="*50)
    print("获取可用巡检模板...")
    templates_res = client.get("/templates/")
    templates = templates_res.json()
    
    if templates:
        print(f"✅ 成功加载 {len(templates)} 种巡检模板:")
        for t in templates:
            print(f"   - [{t['id']}] {t['name']} (周期 {t['period_hours']}h) -> 检查项: {', '.join(t['items'])}")
    else:
        print("❌ 未加载到模板，正在退出...")
        exit(1)

    template_ids = [t['id'] for t in templates]

    # 分配模板
    test_devices = [
        {"name": "主发电机组 Alpha", "sn": "GEN-1001A", "template_ids": [template_ids[0]]},
        {"name": "冷却水泵 B区", "sn": "PMP-2055B", "template_ids": [template_ids[1]]},
        {"name": "高压备用变压器 T1", "sn": "TRF-9090X", "template_ids": [template_ids[0]]},
        {"name": "数控机床 NC-01", "sn": "CNC-8822A", "template_ids": [template_ids[1]]},
        {"name": "叉车 F-102", "sn": "FRK-9002", "template_ids": [template_ids[2]]},
    ]

    print("="*50)
    print("开始添加测试设备并执行模板绑定...")
    for dev in test_devices:
        response = client.post("/devices/", json=dev)
        if response.status_code == 200:
            data = response.json()
            tpl_names = [t['name'] for t in data['templates']] if data['templates'] else ["无模板"]
            overdue_status = "⚠️ 待检查(逾期)" if data['is_overdue'] else "✅ 正常"
            
            print(f"✅ 设备: {data['name']}")
            print(f"   [策略绑定] -> {', '.join(tpl_names)}")
            print(f"   [当前排查状态] -> {overdue_status}")
            print("-" * 50)
        else:
            print(f"❌ 注册失败 ({dev['name']}): {response.text}")
            
    print("新解耦架构数据测试初始化完毕！")
