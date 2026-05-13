import os
import uuid
import json
from typing import Generator, List, Optional
from datetime import datetime, timedelta

from fastapi import FastAPI, Depends, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, Column, String, DateTime, ForeignKey, Integer, JSON, Boolean, Table
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship, joinedload
import qrcode
import requests
from apscheduler.schedulers.background import BackgroundScheduler
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================================================================
# 1. 基础配置与准备
# ==============================================================================
# 为避免受到旧版本数据库结构干扰，这里升级使用 v2 数据库
DATABASE_URL = "sqlite:///./device_mvp_v3.db"
STATIC_DIR = "static"
QRCODE_DIR = os.path.join(STATIC_DIR, "qrcodes")

os.makedirs(QRCODE_DIR, exist_ok=True)
# 配置当前站点的基础访问域名（用于钉钉消息中的链接跳转，请务必使用手机可访问的本机 IP，如 10.120.65.231）
BASE_URL = "http://10.120.65.231:8000"

# ---- 钉钉企业内部应用配置（统一用于消息推送 + 通讯录同步） ----
DINGTALK_CORP_ID = "ding2798dbeb0a3ff5c435c2f4657eb6378f"
DINGTALK_APP_KEY = "dingp68eiv9wz8ltswvp"
DINGTALK_APP_SECRET = "u89RuI7-k01bjw1Ya4M-OqQX-R3D0GhaGsCV1_JRyVE555hl7kqbcTPVBfGTvdTb"
DINGTALK_ROBOT_CODE = "dingp68eiv9wz8ltswvp"  # 应用机器人的 RobotCode（与 AppKey 一致）

# ==============================================================================
# 2. 数据库配置与模型 (通过 SQLAlchemy 实现表关联)
# ==============================================================================
engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False} 
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

device_template_link = Table(
    'device_template_link', Base.metadata,
    Column('device_id', String(36), ForeignKey('devices.id'), primary_key=True),
    Column('template_id', Integer, ForeignKey('inspection_templates.id'), primary_key=True)
)

class InspectionTemplate(Base):
    """检查模板数据模型 (InspectionTemplate)"""
    __tablename__ = "inspection_templates"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    # 存储 JSON 格式的检查项列表，如: ["检查油位", "清理灰尘"]
    items = Column(JSON, nullable=False) 
    # 模板检查周期（天），决定该类设备的检验频次
    period_days = Column(Integer, default=1) 
    # 定时推送时间，格式如 "16:00"
    push_time = Column(String(10), default="08:00")
    
    devices = relationship("Device", secondary=device_template_link, back_populates="templates")

device_maintenance_link = Table(
    'device_maintenance_link', Base.metadata,
    Column('device_id', String(36), ForeignKey('devices.id'), primary_key=True),
    Column('maintenance_id', Integer, ForeignKey('maintenance_plans.id'), primary_key=True)
)

class MaintenancePlan(Base):
    """维护计划数据模型 (MaintenancePlan)"""
    __tablename__ = "maintenance_plans"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    # 存储 JSON 格式的维护项列表
    items = Column(JSON, nullable=False) 
    # 周期（月）
    period_months = Column(Integer, default=1) 
    # 推送日期（每月的几号）
    push_day = Column(Integer, default=28)
    # 推送时间 
    push_time = Column(String(10), default="08:30")
    
    devices = relationship("Device", secondary=device_maintenance_link, back_populates="maintenance_plans")

class Device(Base):
    """设备数据模型 (Device)"""
    __tablename__ = "devices"

    id = Column(String(36), primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    # 扩展字段 (对应 Excel 导入)
    asset_no = Column(String(100), nullable=True) # 固定资产编号
    spec = Column(String(100), nullable=True)     # 规格型号
    manufacturer = Column(String(100), nullable=True) # 生产厂家
    purchase_date = Column(String(50), nullable=True) # 入厂日期
    final_inspection_date = Column(String(50), nullable=True) # 终验通过日期
    location = Column(String(100), nullable=True) # 放置地点
    useful_life = Column(String(50), nullable=True) # 使用年限
    usage_status = Column(String(50), nullable=True) # 使用状况
    dept = Column(String(100), nullable=True)    # 负责部门
    grade = Column(String(20), nullable=True)     # 设备等级
    maintenance_leader = Column(String(50), nullable=True) # 维修班长
    
    sn = Column(String(100), unique=True, nullable=True)
    status = Column(String(50), default="运行中")
    qr_code_path = Column(String(255), nullable=True)
    last_inspection_time = Column(DateTime, nullable=True)
    last_maintenance_time = Column(DateTime, nullable=True)
    inspector_id = Column(String(50), ForeignKey("users.id"), nullable=True)
    last_notified_time = Column(DateTime, nullable=True) # 检查通知节流
    last_maintenance_notified_time = Column(DateTime, nullable=True) # 维保通知节流
    
    maintenance_leader_id = Column(String(50), ForeignKey("users.id"), nullable=True)

    # 反向关联
    inspector = relationship("User", foreign_keys=[inspector_id])
    maintenance_leader_obj = relationship("User", foreign_keys=[maintenance_leader_id])
    templates = relationship("InspectionTemplate", secondary=device_template_link, back_populates="devices")
    maintenance_plans = relationship("MaintenancePlan", secondary=device_maintenance_link, back_populates="devices")

    @property
    def is_overdue(self) -> bool:
        """核心漏检提醒逻辑：动态计算是否待检查 (逾期)"""
        if not self.templates:
            return False
        if not self.last_inspection_time:
            return True
        # 逾期逻辑：当前时间距离上次检查超过了设定的天数
        return any(datetime.now() >= self.last_inspection_time + timedelta(days=t.period_days) for t in self.templates)

    @property
    def is_m_overdue(self) -> bool:
        """核心维保到期提醒逻辑：计算是否待维护 (逾期)"""
        if not self.maintenance_plans:
            return False
        if not self.last_maintenance_time:
            return True
        now_dt = datetime.now()
        for p in self.maintenance_plans:
            # 计算已经过去的自然月总数
            months_passed = (now_dt.year - self.last_maintenance_time.year) * 12 + now_dt.month - self.last_maintenance_time.month
            
            if months_passed > p.period_months:
                return True
            # 如果到达了指定的月数，且目前日期已经 >= 设置的推送日，则也认为已到期
            if months_passed == p.period_months and now_dt.day >= p.push_day:
                return True
        return False

class InspectionRecord(Base):
    """检查记录数据模型 (InspectionRecord)"""
    __tablename__ = "inspection_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    device_id = Column(String(36), ForeignKey("devices.id"), nullable=False)
    template_id = Column(Integer, ForeignKey("inspection_templates.id"), nullable=True) # 本次执行的具体检查计划ID
    inspector = Column(String(50), default="检查员")
    checklist = Column(JSON, nullable=False)  # 存储每一次具体的检查项布尔值结果
    remarks = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    # 关联数据
    device = relationship("Device")
    template = relationship("InspectionTemplate")

class Department(Base):
    """部门数据模型 (同步钉钉架构)"""
    __tablename__ = "departments"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    parent_id = Column(Integer, nullable=True)

class User(Base):
    """人员数据模型 (同步钉钉用户)"""
    __tablename__ = "users"
    id = Column(String(50), primary_key=True, index=True) # 钉钉通讯录 UserID
    name = Column(String(50), nullable=False)
    avatar = Column(String(255), nullable=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True)
    job_title = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True)
    
    department = relationship("Department")

class WorkOrder(Base):
    """设备报修工单 (WorkOrder)"""
    __tablename__ = "work_orders"
    
    id = Column(String(36), primary_key=True)
    device_id = Column(String(36), ForeignKey("devices.id"), nullable=False)
    reporter_id = Column(String(50), nullable=True) # 报修人 (钉钉 userid)
    leader_id = Column(String(50), nullable=True) # 维修班长 (钉钉 userid)
    repairman_id = Column(String(50), nullable=True) # 派单给的维修人 (钉钉 userid)
    description = Column(String(500), nullable=False) # 故障描述
    repair_notes = Column(String(500), nullable=True) # 维修记录
    status = Column(String(20), default="待处理") # 待处理, 维修中, 已完成
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    device = relationship("Device")
    reporter = relationship("User", primaryjoin="WorkOrder.reporter_id == User.id", foreign_keys=[reporter_id])
    leader = relationship("User", primaryjoin="WorkOrder.leader_id == User.id", foreign_keys=[leader_id])
    repairman = relationship("User", primaryjoin="WorkOrder.repairman_id == User.id", foreign_keys=[repairman_id])

class SystemLog(Base):
    """系统日志数据模型 (记录全周期事件)"""
    __tablename__ = "system_logs"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    device_id = Column(String(36), ForeignKey("devices.id"), nullable=True)
    event_type = Column(String(20)) # 检查, 报修, 派单, 完工
    operator = Column(String(100)) # 操作人
    content = Column(String(2000)) # 日志详情内容
    created_at = Column(DateTime, default=datetime.now)
    
    device = relationship("Device")

Base.metadata.create_all(bind=engine)


# ==============================================================================
# 3. Pydantic 数据模式 (自动包含关联的嵌套模型解析)
# ==============================================================================
class TemplateResponse(BaseModel):
    id: int
    name: str
    items: list[str]
    period_days: int
    push_time: str
    model_config = ConfigDict(from_attributes=True)

class TemplateCreate(BaseModel):
    name: str
    items: list[str]
    period_days: int
    push_time: str

class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    items: Optional[list[str]] = None
    period_days: Optional[int] = None
    push_time: Optional[str] = None

class MaintenancePlanCreate(BaseModel):
    name: str
    items: List[str]
    period_months: int
    push_day: int
    push_time: str

class MaintenancePlanResponse(BaseModel):
    id: int
    name: str
    items: List[str]
    period_months: int
    push_day: int
    push_time: str
    model_config = ConfigDict(from_attributes=True)

class DeviceCreate(BaseModel):
    name: str
    sn: str
    template_ids: list[int] = []
    maintenance_plan_ids: list[int] = []
    inspector_id: Optional[str] = None
    maintenance_leader_id: Optional[str] = None
    asset_no: Optional[str] = None
    spec: Optional[str] = None
    manufacturer: Optional[str] = None
    purchase_date: Optional[str] = None
    final_inspection_date: Optional[str] = None
    location: Optional[str] = None
    useful_life: Optional[str] = None
    usage_status: Optional[str] = None
    dept: Optional[str] = None
    grade: Optional[str] = None
    maintenance_leader: Optional[str] = None

class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    sn: Optional[str] = None
    status: Optional[str] = None
    template_ids: Optional[list[int]] = None
    maintenance_plan_ids: Optional[list[int]] = None
    inspector_id: Optional[str] = None
    maintenance_leader_id: Optional[str] = None
    asset_no: Optional[str] = None
    spec: Optional[str] = None
    manufacturer: Optional[str] = None
    purchase_date: Optional[str] = None
    final_inspection_date: Optional[str] = None
    location: Optional[str] = None
    useful_life: Optional[str] = None
    usage_status: Optional[str] = None
    dept: Optional[str] = None
    grade: Optional[str] = None
    maintenance_leader: Optional[str] = None

class DeviceBindTemplate(BaseModel):
    template_ids: list[int]

class ChecklistItem(BaseModel):
    item_name: str
    is_normal: bool

class InspectionSubmit(BaseModel):
    device_id: str
    template_id: Optional[int] = None
    checklist: list[ChecklistItem]
    remarks: Optional[str] = None
    timestamp: Optional[str] = None

class RecordDeviceResponse(BaseModel):
    name: str
    sn: str
    model_config = ConfigDict(from_attributes=True)

class InspectionRecordResponse(BaseModel):
    id: int
    device_id: str
    template_id: Optional[int] = None
    inspector: str
    checklist: list[dict]
    remarks: Optional[str] = None
    created_at: datetime
    device: Optional[RecordDeviceResponse] = None
    model_config = ConfigDict(from_attributes=True)

class DeptResponse(BaseModel):
    id: int
    name: str
    parent_id: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)

class UserResponse(BaseModel):
    id: str
    name: str
    avatar: Optional[str] = None
    job_title: Optional[str] = None
    department: Optional[DeptResponse] = None
    model_config = ConfigDict(from_attributes=True)

class DeviceResponse(BaseModel):
    id: str
    name: Optional[str] = None
    sn: Optional[str] = None
    status: Optional[str] = None
    qr_code_path: Optional[str] = None
    last_inspection_time: Optional[datetime] = None
    last_maintenance_time: Optional[datetime] = None
    inspector_id: Optional[str] = None
    maintenance_leader_id: Optional[str] = None
    
    is_overdue: bool
    is_m_overdue: bool
    # 嵌套返回关联的模板、维护计划和人员内容
    templates: list[TemplateResponse] = []
    maintenance_plans: list[MaintenancePlanResponse] = []
    inspector: Optional[UserResponse] = None

    asset_no: Optional[str] = None
    spec: Optional[str] = None
    manufacturer: Optional[str] = None
    purchase_date: Optional[str] = None
    final_inspection_date: Optional[str] = None
    location: Optional[str] = None
    useful_life: Optional[str] = None
    usage_status: Optional[str] = None
    dept: Optional[str] = None
    grade: Optional[str] = None
    maintenance_leader: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)

class LoginData(BaseModel):
    username: str
    password: str

# --- 报修相关模型 ---
class WorkOrderCreate(BaseModel):
    device_id: str
    reporter_id: str
    leader_id: str
    description: str

class WorkOrderDispatch(BaseModel):
    repairman_id: str

class WorkOrderResponse(BaseModel):
    id: str
    device_id: str
    reporter_id: Optional[str] = None
    leader_id: Optional[str] = None
    repairman_id: Optional[str] = None
    description: str
    repair_notes: Optional[str] = None
    status: str
    created_at: datetime
    device: Optional[DeviceResponse] = None
    reporter: Optional[UserResponse] = None
    model_config = ConfigDict(from_attributes=True)

class LogResponse(BaseModel):
    id: int
    device_id: Optional[str] = None
    event_type: str
    operator: str
    content: str
    created_at: datetime
    device: Optional[DeviceResponse] = None
    model_config = ConfigDict(from_attributes=True)


# ==============================================================================
# 4. FastAPI 应用初始化及预设数据
# ==============================================================================
app = FastAPI(title="设备管理系统 MVP - 复杂业务流版", version="2.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/", summary="根路径重定向")
def root_redirect():
    return RedirectResponse(url="/dashboard")

@app.on_event("startup")
def init_default_data():
    """初始化预设的一些检查模板，方便测试直接调用"""
    db = SessionLocal()
    try:
        if db.query(InspectionTemplate).count() == 0:
            db.add(InspectionTemplate(name="配电箱基础检查", items=["检查外壳是否漏电", "空开状态正常", "清理灰尘"], period_days=1, push_time="16:00"))
            db.add(InspectionTemplate(name="空压机日常检查", items=["检查油位", "排气压力达标", "无异常颤动", "滤芯清洁"], period_days=1, push_time="08:00"))
            db.add(InspectionTemplate(name="叉车安全点检", items=["电量/油量充足", "刹车灵活", "升降液压防漏", "轮胎气压正常"], period_days=1, push_time="09:00"))
            db.commit()
        if db.query(MaintenancePlan).count() == 0:
            db.add(MaintenancePlan(name="月度定期维护计划", items=["更换润滑维持运转", "紧固关键螺栓", "电气线路清灰", "易损件磨损检查"], period_months=1, push_day=28, push_time="08:30"))
            db.commit()
    finally:
        db.close()

# ======== APScheduler 钉钉应用机器人消息定时任务 ========
scheduler = BackgroundScheduler()


def get_dingtalk_access_token():
    """获取钉钉企业内部应用的 access_token"""
    url = "https://oapi.dingtalk.com/gettoken"
    params = {"appkey": DINGTALK_APP_KEY, "appsecret": DINGTALK_APP_SECRET}
    try:
        res = requests.get(url, params=params, timeout=10).json()
        if res.get("errcode") != 0:
            logger.error(f"钉钉凭证获取失败: {res.get('errmsg')}")
            return None
        return res["access_token"]
    except Exception as e:
        logger.error(f"网脉异常，获取钉钉 token 失败: {str(e)}")
        return None


def send_dingtalk_robot_message(access_token: str, user_ids: list, title: str, content: str):
    """
    通过钉钉企业内部应用机器人，向指定用户发送单聊 Markdown 消息。
    API: POST https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend
    限制: 单次最多 20 个用户
    """
    url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
    headers = {
        "x-acs-dingtalk-access-token": access_token,
        "Content-Type": "application/json"
    }
    payload = {
        "robotCode": DINGTALK_ROBOT_CODE,
        "userIds": user_ids,
        "msgKey": "sampleMarkdown",
        "msgParam": json.dumps({"title": title, "text": content})
    }
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        result = res.json()
        # 新版 API 成功时不返回 errcode，而是返回 processQueryKey
        if "processQueryKey" in result:
            logger.info(f"-> 钉钉机器人推送成功: 用户 {user_ids}")
            return True
        else:
            logger.warning(f"-> 钉钉机器人推送失败: {result}")
            return False
    except Exception as e:
        logger.error(f"-> 钉钉机器人推送异常: {str(e)}")
        return False


def check_overdue_and_notify():
    """定时检查逾期设备并通过钉钉应用机器人逐人推送提醒（区分检查与维护）"""
    logger.info("开始系统周期任务：检查逾期及维保到期提醒...")
    db = SessionLocal()
    try:
        devices = db.query(Device).options(
            joinedload(Device.templates), 
            joinedload(Device.maintenance_plans), 
            joinedload(Device.inspector),
            joinedload(Device.maintenance_leader_obj)
        ).all()
        
        now_dt = datetime.now()
        current_time = now_dt.strftime("%H:%M")
        
        # 用户字典：userid -> { 'inspections': [list], 'maintenances': [list], 'name': 'xxx' }
        notifications = {}

        def add_notification(uid, d, type_key, name):
            if uid not in notifications:
                notifications[uid] = {'inspections': [], 'maintenances': [], 'name': name}
            if d not in notifications[uid][type_key]: # 避免重复加入
                notifications[uid][type_key].append(d)

        for d in devices:
            # 1. 检查巡检逾期逻辑：不仅要过期，还要到设定的推送时间
            if d.inspector_id and d.templates:
                for t in d.templates:
                    # 如果匹配到推送时间，且已逾期，且今天没推送过
                    if current_time == t.push_time and d.is_overdue:
                        # 节流判断：如果 23 小时内没推送过 (给分钟扫描一点余量)
                        if not d.last_notified_time or now_dt > d.last_notified_time + timedelta(hours=23):
                            add_notification(d.inspector_id, d, 'inspections', d.inspector.name if d.inspector else "同事")
            
            # 2. 检查维保逾期逻辑：到达号数、且到达特定推送时间
            if d.maintenance_leader_id and d.maintenance_plans:
                for p in d.maintenance_plans:
                    if current_time == p.push_time and now_dt.day == p.push_day and d.is_m_overdue:
                        if not d.last_maintenance_notified_time or now_dt > d.last_maintenance_notified_time + timedelta(hours=23):
                            add_notification(d.maintenance_leader_id, d, 'maintenances', d.maintenance_leader_obj.name if d.maintenance_leader_obj else "班长")

        if notifications:
            access_token = get_dingtalk_access_token()
            if not access_token:
                logger.error("无法获取钉钉 access_token，跳过推送")
                return

            for uid, data in notifications.items():
                lines = [f"### 🔔 资产管理预警通知\n"]
                lines.append(f"**{data['name']}** 您好，由您负责的资产有新动态：\n")
                
                if data['inspections']:
                    lines.append(f"#### 🚨 检查逾期待办")
                    for d in data['inspections']:
                        lines.append(f"- {d.name}（SN: `{d.sn}`）")
                
                if data['maintenances']:
                    lines.append(f"\n#### 🛠️ 维保到期提醒")
                    for d in data['maintenances']:
                        lines.append(f"- {d.name}（SN: `{d.sn}`）")
                
                lines.append(f"\n> 请尽快登录系统完成相关操作！")
                
                content = "\n".join(lines)
                success = send_dingtalk_robot_message(
                    access_token, [uid],
                    title="资产管理到期预警",
                    content=content
                )
                
                if success:
                    # 更新数据库节流标记
                    for d in data['inspections']:
                        d.last_notified_time = datetime.now()
                    for d in data['maintenances']:
                        d.last_maintenance_notified_time = datetime.now()
        
        db.commit()
    finally:
        db.close()

# 加入调度器，演示环境可设置为每分钟执行一次
scheduler.add_job(check_overdue_and_notify, 'interval', minutes=1)
scheduler.start()


# ==============================================================================
# 5. 辅助与工具函数
# ==============================================================================
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def generate_inspection_qr_code(device_id: str) -> str:
    inspection_url = f"{BASE_URL}/inspect/{device_id}"
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(inspection_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    filename = f"{device_id}.png"
    filepath = os.path.join(QRCODE_DIR, filename)
    img.save(filepath)
    return f"static/qrcodes/{filename}"


# ==============================================================================
# 6. API 路由与业务逻辑：设备、模板更换、高并发读取优化
# ==============================================================================
@app.get("/templates/", response_model=List[TemplateResponse], summary="获取所有排班模板")
def list_templates(db: Session = Depends(get_db)):
    """提供给管理后台供拉取模板列表用于分配"""
    return db.query(InspectionTemplate).all()

@app.post("/templates/", response_model=TemplateResponse, summary="新增检查模板")
def create_template(tpl_in: TemplateCreate, db: Session = Depends(get_db)):
    new_tpl = InspectionTemplate(
        name=tpl_in.name, 
        items=tpl_in.items, 
        period_days=tpl_in.period_days,
        push_time=tpl_in.push_time
    )
    db.add(new_tpl)
    db.commit()
    db.refresh(new_tpl)
    return new_tpl

@app.put("/templates/{template_id}", response_model=TemplateResponse, summary="修改检查模板")
def update_template(template_id: int, payload: TemplateUpdate, db: Session = Depends(get_db)):
    tpl = db.query(InspectionTemplate).filter(InspectionTemplate.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="未找到对应的模板。")
    if payload.name is not None:
        tpl.name = payload.name
    if payload.items is not None:
        tpl.items = payload.items
    if payload.period_days is not None:
        tpl.period_days = payload.period_days
    if payload.push_time is not None:
        tpl.push_time = payload.push_time
    db.commit()
    db.refresh(tpl)
    return tpl

@app.delete("/templates/{template_id}", summary="删除检查模板")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    tpl = db.query(InspectionTemplate).options(joinedload(InspectionTemplate.devices)).filter(InspectionTemplate.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="未找到该模板。")
    # 级联处理：通过多对多关系自动解除设备与该模板的绑定
    tpl.devices.clear()
    db.delete(tpl)
    db.commit()
    return {"message": "模板已成功删除"}

@app.post("/devices/", response_model=DeviceResponse, summary="添加新设备")
def create_device(device_in: DeviceCreate, db: Session = Depends(get_db)):
    db_device = db.query(Device).filter(Device.sn == device_in.sn).first()
    if db_device:
        raise HTTPException(status_code=400, detail="序列号(SN)已存在。")
    
    # 如果指定了模板，检查它存在与否
    templates_db = []
    if device_in.template_ids:
        templates_db = db.query(InspectionTemplate).filter(InspectionTemplate.id.in_(device_in.template_ids)).all()
        if len(templates_db) != len(device_in.template_ids):
            raise HTTPException(status_code=400, detail="指定的某些检查模板ID不存在。")
    
    # 增加维护计划绑定支持
    maintenance_plans_db = []
    if device_in.maintenance_plan_ids:
        maintenance_plans_db = db.query(MaintenancePlan).filter(MaintenancePlan.id.in_(device_in.maintenance_plan_ids)).all()
        if len(maintenance_plans_db) != len(device_in.maintenance_plan_ids):
            raise HTTPException(status_code=400, detail="指定的某些维护计划ID不存在。")

    device_id = str(uuid.uuid4())
    qr_path = generate_inspection_qr_code(device_id)
    
    new_device = Device(
        id=device_id,
        name=device_in.name,
        sn=device_in.sn,
        status="运行中",
        qr_code_path=qr_path,
        inspector_id=device_in.inspector_id,
        templates=templates_db,
        maintenance_plans=maintenance_plans_db,
        asset_no=device_in.asset_no,
        spec=device_in.spec,
        manufacturer=device_in.manufacturer,
        purchase_date=device_in.purchase_date,
        final_inspection_date=device_in.final_inspection_date,
        location=device_in.location,
        useful_life=device_in.useful_life,
        usage_status=device_in.usage_status,
        dept=device_in.dept,
        grade=device_in.grade,
        maintenance_leader=device_in.maintenance_leader,
        maintenance_leader_id=device_in.maintenance_leader_id
    )
    
    db.add(new_device)
    db.commit()
    
    return db.query(Device).options(
        joinedload(Device.templates), 
        joinedload(Device.maintenance_plans),
        joinedload(Device.inspector),
        joinedload(Device.maintenance_leader_obj)
    ).filter(Device.id == device_id).first()

@app.get("/devices/", response_model=List[DeviceResponse], summary="获取所有设备列表")
def list_devices(db: Session = Depends(get_db)):
    """联表获取设备及绑定的模板和责任人数据，规避 N+1 查询"""
    try:
        devices = db.query(Device).options(
            joinedload(Device.templates), 
            joinedload(Device.maintenance_plans),
            joinedload(Device.inspector),
            joinedload(Device.maintenance_leader_obj)
        ).all()
        return devices
    except Exception as e:
        logger.exception(f"获取设备列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"内部错误: {str(e)}")

@app.get("/devices/{device_id}", response_model=DeviceResponse, summary="获取指定设备明细")
def read_device(device_id: str, db: Session = Depends(get_db)):
    device = db.query(Device).options(
        joinedload(Device.templates), 
        joinedload(Device.maintenance_plans),
        joinedload(Device.inspector)
    ).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="未找到设备信息。")
    return device

@app.put("/devices/{device_id}/template", response_model=DeviceResponse, summary="为设备更换/绑定检查模板")
def update_device_template(device_id: str, payload: DeviceBindTemplate, db: Session = Depends(get_db)):
    device = db.query(Device).options(joinedload(Device.templates)).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="未找到设备。")
        
    templates_db = db.query(InspectionTemplate).filter(InspectionTemplate.id.in_(payload.template_ids)).all()
    if len(templates_db) != len(payload.template_ids):
        raise HTTPException(status_code=404, detail="包含未找到的指定模板ID。")
        
    device.templates = templates_db
    db.commit()
    db.refresh(device)
    return device

@app.put("/devices/{device_id}", response_model=DeviceResponse, summary="修改设备基础信息")
def update_device(device_id: str, payload: DeviceUpdate, db: Session = Depends(get_db)):
    """允许在管理后台编辑设备的名称、SN 码、状态以及关联的模板和责任人"""
    device = db.query(Device).options(
        joinedload(Device.templates), 
        joinedload(Device.maintenance_plans),
        joinedload(Device.inspector)
    ).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="未找到对应的设备。")
        
    if payload.sn and payload.sn != device.sn:
        if db.query(Device).filter(Device.sn == payload.sn).first():
            raise HTTPException(status_code=400, detail="新的序列号(SN)已存在，不可重复。")
            
    if payload.name is not None:
        device.name = payload.name
    if payload.sn is not None:
        device.sn = payload.sn
    if payload.status is not None:
        device.status = payload.status
    
    if payload.template_ids is not None:
        templates_db = db.query(InspectionTemplate).filter(InspectionTemplate.id.in_(payload.template_ids)).all()
        if len(templates_db) != len(payload.template_ids):
            raise HTTPException(status_code=404, detail="包含未找到的指定模板ID。")
        device.templates = templates_db

    if payload.maintenance_plan_ids is not None:
        m_plans_db = db.query(MaintenancePlan).filter(MaintenancePlan.id.in_(payload.maintenance_plan_ids)).all()
        if len(m_plans_db) != len(payload.maintenance_plan_ids):
            raise HTTPException(status_code=404, detail="包含未找到的指定维护计划ID。")
        device.maintenance_plans = m_plans_db

    if payload.inspector_id is not None:
        if payload.inspector_id != "":
            device.inspector_id = payload.inspector_id
        else:
            device.inspector_id = None

    if payload.maintenance_leader_id is not None:
        if payload.maintenance_leader_id != "":
            device.maintenance_leader_id = payload.maintenance_leader_id
        else:
            device.maintenance_leader_id = None
            
    # 扩展字段更新
    if payload.asset_no is not None: device.asset_no = payload.asset_no
    if payload.spec is not None: device.spec = payload.spec
    if payload.manufacturer is not None: device.manufacturer = payload.manufacturer
    if payload.purchase_date is not None: device.purchase_date = payload.purchase_date
    if payload.final_inspection_date is not None: device.final_inspection_date = payload.final_inspection_date
    if payload.location is not None: device.location = payload.location
    if payload.useful_life is not None: device.useful_life = payload.useful_life
    if payload.usage_status is not None: device.usage_status = payload.usage_status
    if payload.dept is not None: device.dept = payload.dept
    if payload.grade is not None: device.grade = payload.grade
    if payload.maintenance_leader is not None: device.maintenance_leader = payload.maintenance_leader
            
    db.commit()
    db.refresh(device)
    return db.query(Device).options(
        joinedload(Device.templates), 
        joinedload(Device.maintenance_plans),
        joinedload(Device.inspector),
        joinedload(Device.maintenance_leader_obj)
    ).filter(Device.id == device_id).first()

@app.delete("/devices/{device_id}", summary="删除设备")
def delete_device(device_id: str, db: Session = Depends(get_db)):
    """彻底删除一台设备及其相关的检查记录约束处理"""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="未找到该设备。")
        
    # 可选：连带清理该设备的检查记录
    db.query(InspectionRecord).filter(InspectionRecord.device_id == device_id).delete()
    
    db.delete(device)
    db.commit()
    return {"message": "设备已成功删除"}

@app.post("/inspections/", response_model=InspectionRecordResponse, summary="提交检查记录")
def submit_inspection(payload: InspectionSubmit, db: Session = Depends(get_db)):
    """
    接收来自手机端的检查提交：
    1. 增加一条检查台账记录
    2. 刷新上级设备的「最后检查时间」
    3. 如果检查项含有异常，联动直接改变设备当前状态为「维护」/ 如果全正常即为「运行中」
    """
    device = db.query(Device).filter(Device.id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="未找到对应的设备。")
        
    # 判断否存在不正常的检查项（is_normal == False）
    has_error = any(not item.is_normal for item in payload.checklist)
    
    # 构建检查列表持久化所需 JSON 数据
    checklist_data = [{"item_name": item.item_name, "is_normal": item.is_normal} for item in payload.checklist]
    # 记录台账日志
    # inspector 暂时默认“检查员”，后续可以通过整合 JWT Token 直接反查
    inspector_name = "检查员"
    record = InspectionRecord(
        device_id=payload.device_id,
        template_id=payload.template_id,
        inspector=inspector_name,
        checklist=[item.model_dump() for item in payload.checklist],
        remarks=payload.remarks
    )
    
    # 将记录挂入 DB Session
    db.add(record)
    
    # 记录系统日志
    log_content = f"执行了手机端日常检查。模板ID: {payload.template_id}。结果: {'⚠️ 包含异常' if has_error else '✅ 一切正常'}。"
    new_log = SystemLog(
        device_id=payload.device_id,
        event_type="检查",
        operator="巡检员",
        content=log_content
    )
    db.add(new_log)
    
    # 联动改写设备的元数据以及状态
    device.last_inspection_time = datetime.now()
    if has_error or (payload.remarks and "漏检" in payload.remarks): # 也可以简单扩展文本风险识别
        device.status = "维护"
    else:
        # 如果是停机状态（可能是报废）则尽量不改，或者我们简单粗暴如果全好就变成“运行中”
        if device.status != "停机":
            device.status = "运行中"
            
    db.commit()
    db.refresh(record)
    # eager load 关联的 device 和 template，避免序列化时懒加载异常
    record = db.query(InspectionRecord).options(
        joinedload(InspectionRecord.device),
        joinedload(InspectionRecord.template)
    ).filter(InspectionRecord.id == record.id).first()
    return record

@app.get("/records/", response_model=List[InspectionRecordResponse], summary="拉取所有检查台账日志")
def list_records(db: Session = Depends(get_db)):
    """返回最新的 200 条检查日志，并使用 joinedload 关联查询提升性能"""
    return db.query(InspectionRecord).options(
        joinedload(InspectionRecord.device),
        joinedload(InspectionRecord.template)
    ).order_by(InspectionRecord.created_at.desc()).limit(200).all()

# --- 维护计划 API ---
@app.post("/maintenance/", response_model=MaintenancePlanResponse, summary="新增维护计划")
def create_maintenance_plan(plan: MaintenancePlanCreate, db: Session = Depends(get_db)):
    db_plan = MaintenancePlan(**plan.model_dump())
    db.add(db_plan)
    db.commit()
    db.refresh(db_plan)
    return db_plan

@app.get("/maintenance/", response_model=List[MaintenancePlanResponse], summary="拉取所有维护计划")
def list_maintenance_plans(db: Session = Depends(get_db)):
    return db.query(MaintenancePlan).all()

@app.put("/maintenance/{plan_id}", response_model=MaintenancePlanResponse, summary="更新维护计划")
def update_maintenance_plan(plan_id: int, plan: MaintenancePlanCreate, db: Session = Depends(get_db)):
    db_plan = db.query(MaintenancePlan).filter(MaintenancePlan.id == plan_id).first()
    if not db_plan:
        raise HTTPException(status_code=404, detail="维护计划不存在")
    
    db_plan.name = plan.name
    db_plan.items = plan.items
    db_plan.period_months = plan.period_months
    db_plan.push_day = plan.push_day
    db_plan.push_time = plan.push_time
    
    db.commit()
    db.refresh(db_plan)
    return db_plan

@app.delete("/maintenance/{plan_id}", summary="删除维护计划")
def delete_maintenance_plan(plan_id: int, db: Session = Depends(get_db)):
    db_plan = db.query(MaintenancePlan).filter(MaintenancePlan.id == plan_id).first()
    if not db_plan:
        raise HTTPException(status_code=404, detail="维护计划不存在")
    try:
        db.delete(db_plan)
        db.commit()
        return {"message": "维护计划已删除"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除维护计划失败: {str(e)}")

# --- 设备报修与工单系统 API ---

@app.post("/repair/", summary="发起设备报修")
def create_repair_order(payload: WorkOrderCreate, db: Session = Depends(get_db)):
    # 1. 查找设备
    device = db.query(Device).filter(Device.id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="未找到设备")
        
    # 2. 修改设备状态为故障
    device.status = "故障"
    
    # 3. 创建工单
    order_id = str(uuid.uuid4())
    new_order = WorkOrder(
        id=order_id,
        device_id=payload.device_id,
        reporter_id=payload.reporter_id,
        leader_id=payload.leader_id,
        description=payload.description,
        status="待处理"
    )
    db.add(new_order)
    
    # 记录系统日志
    log_content = f"发起了设备故障报修。报修原因: {payload.description}"
    reporter = db.query(User).filter(User.id == payload.reporter_id).first()
    operator_name = reporter.name if reporter else "匿名报修人"
    new_log = SystemLog(
        device_id=payload.device_id,
        event_type="报修",
        operator=operator_name,
        content=log_content
    )
    db.add(new_log)

    db.commit()
    db.refresh(new_order)
    
    # 4. 钉钉推送给维修班长 (增加容错)
    try:
        token = get_dingtalk_access_token()
        if token and payload.leader_id:
            title = "📢 设备故障报修通知"
            content = f"### 设备异常报修\n\n" \
                      f"**设备名称**: {device.name}\n\n" \
                      f"**故障描述**: {payload.description}\n\n" \
                      f"**工单状态**: 待处理\n\n" \
                      f"[点击查看并派单]({BASE_URL}/order/{order_id})"
            send_dingtalk_robot_message(token, [payload.leader_id], title, content)
    except Exception as e:
        logger.error(f"报修工单创建后的消息推送出现异常: {str(e)}")
        # 即使推送失败，我们也返回 200，保证数据库已落位
        
    return {"message": "报修成功，已通知维修班长", "order_id": order_id}

@app.get("/orders/{order_id}", response_model=WorkOrderResponse, summary="获取工单详情")
def get_work_order_detail(order_id: str, db: Session = Depends(get_db)):
    order = db.query(WorkOrder).options(
        joinedload(WorkOrder.device),
        joinedload(WorkOrder.reporter)
    ).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="工单不存在")
    return order

@app.post("/orders/{order_id}/dispatch", summary="维修班长派单")
def dispatch_work_order(order_id: str, payload: WorkOrderDispatch, db: Session = Depends(get_db)):
    order = db.query(WorkOrder).options(joinedload(WorkOrder.device)).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="工单不存在")
    
    order.repairman_id = payload.repairman_id
    order.status = "维修中"
    
    # 记录系统日志
    repairman = db.query(User).filter(User.id == payload.repairman_id).first()
    r_name = repairman.name if repairman else "未知人员"
    log_content = f"班长派发了维修任务给 {r_name}。故障详情: {order.description}"
    new_log = SystemLog(
        device_id=order.device_id,
        event_type="派单",
        operator="维修班长",
        content=log_content
    )
    db.add(new_log)

    db.commit()
    
    # 推送至维修人员
    token = get_dingtalk_access_token()
    if token and payload.repairman_id:
        title = "🛠️ 维修派单任务"
        content = f"### 收到新的维修派单\n\n" \
                  f"**设备名称**: {order.device.name}\n\n" \
                  f"**故障描述**: {order.description}\n\n" \
                  f"**工单状态**: 维修中\n\n" \
                  f"[点击查看工单详情]({BASE_URL}/order/{order_id})"
        send_dingtalk_robot_message(token, [payload.repairman_id], title, content)
        
    return {"message": "已成功派单"}

@app.post("/orders/{order_id}/complete", summary="维修人员完成维修")
def complete_work_order(order_id: str, payload: dict = None, db: Session = Depends(get_db)):
    order = db.query(WorkOrder).options(joinedload(WorkOrder.device)).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="工单不存在")
    
    order.status = "已完成"
    if payload and "repair_notes" in payload:
        order.repair_notes = payload["repair_notes"]
    
    # 修改设备状态回正常 (此处统一设为运行中)
    device = db.query(Device).filter(Device.id == order.device_id).first()
    if device:
        device.status = "运行中"
        device.last_maintenance_time = datetime.now()
    
    # 记录系统日志
    r_notes = payload.get("repair_notes", "未填写备注") if payload else "无备注"
    log_content = f"维修人员已完成现场修复，设备重置为正常状态。维修备注: {r_notes}"
    repairman = db.query(User).filter(User.id == order.repairman_id).first()
    op_name = repairman.name if repairman else "维修人员"
    new_log = SystemLog(
        device_id=order.device_id,
        event_type="完工",
        operator=op_name,
        content=log_content
    )
    db.add(new_log)

    db.commit()
    
    # 推送至报修人
    token = get_dingtalk_access_token()
    if token and order.reporter_id:
        title = "✅ 设备维修完成通知"
        content = f"### 您的报修已修复\n\n" \
                  f"**设备名称**: {order.device.name}\n\n" \
                  f"**修复状态**: 已重新运行\n\n" \
                  f"**处理人**: 系统通知"
        send_dingtalk_robot_message(token, [order.reporter_id], title, content)
        
    return {"message": "维修工单已完成"}

@app.get("/logs/", response_model=List[LogResponse], summary="拉取系统全量操作日志")
def list_system_logs(db: Session = Depends(get_db)):
    """获取所有生命周期日志并由新到旧排序"""
    logs = db.query(SystemLog).options(joinedload(SystemLog.device)).order_by(SystemLog.created_at.desc()).all()
    return logs

@app.post("/dingtalk/sync", summary="触发钉钉组织架构同步")
def sync_dingtalk_org(db: Session = Depends(get_db)):
    """
    钉钉通讯录同步 API（增量更新模式）:
    1. 调用 https://oapi.dingtalk.com/gettoken 获取 access_token
    2. 拉取全量部门 /department/list
    3. 遍历各部门拉取人员 /user/listbypage
    4. 增量更新：以钉钉数据为准进行更新或新增，但不删除本地已有的人员和绑定关系
    """
    access_token = get_dingtalk_access_token()
    if not access_token:
        raise HTTPException(status_code=400, detail="钉钉凭证获取失败，请检查 AppKey/AppSecret 配置")
    
    # [1] 拉取系统各层级全量部门
    # 钉钉根部门 ID 默认为 1
    dept_url = f"https://oapi.dingtalk.com/department/list?access_token={access_token}&fetch_child=true&id=1"
    dept_res = requests.get(dept_url, timeout=10).json()
    if dept_res.get("errcode") != 0:
        raise HTTPException(status_code=400, detail=f"拉取钉钉部门列表失败: {dept_res.get('errmsg')}")
        
    departments = dept_res.get("department", [])

    # [2] 遍历每个部门，通过分页获取全量人员
    all_users = {}
    for dept in departments:
        offset = 0
        page_size = 100
        while True:
            user_url = (
                f"https://oapi.dingtalk.com/user/listbypage"
                f"?access_token={access_token}&department_id={dept['id']}"
                f"&offset={offset}&size={page_size}"
            )
            user_res = requests.get(user_url, timeout=10).json()
            if user_res.get("errcode") != 0:
                logger.warning(f"拉取部门 {dept['name']}({dept['id']}) 人员失败: {user_res.get('errmsg')}")
                break
            user_list = user_res.get("userlist", [])
            for u in user_list:
                all_users[u["userid"]] = u
            if not user_res.get("hasMore", False):
                break
            offset += page_size

    # [3] 增量同步模式：以钉钉数据为准进行更新或新增，但不删除本地已有的人员和绑定关系
    # 这样可以保留后续 Excel 导入的人员或手动分配的责任人

    # [4] 写入/更新 最新部门
    for d in departments:
        db_dept = db.query(Department).filter(Department.id == d["id"]).first()
        if not db_dept:
            db.add(Department(id=d["id"], name=d["name"], parent_id=d.get("parentid", 0)))
        else:
            db_dept.name = d["name"]
            db_dept.parent_id = d.get("parentid", 0)
    db.commit()  # 先 commit 保证员工可以安全关联到有效部门外键

    # [5] 写入/更新 最新人员
    for u in all_users.values():
        db_user = db.query(User).filter(User.id == u["userid"]).first()
        
        dept_list = u.get("department", [])
        main_dept = dept_list[0] if dept_list else None
        job_title = u.get("position", "员工") or "员工"
        
        user_data = {
            "name": u.get("name", ""),
            "avatar": u.get("avatar", ""),
            "department_id": main_dept,
            "job_title": job_title,
            "is_active": u.get("active", True)
        }
        
        if not db_user:
            db.add(User(id=u["userid"], **user_data))
        else:
            for k, v in user_data.items():
                setattr(db_user, k, v)
                
    db.commit()
    return {"message": "钉钉通讯录同步成功（增量更新）", "dept_count": len(departments), "user_count": len(all_users)}

@app.post("/devices/import", summary="通过上传 Excel 批量导入设备")
def import_devices_from_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    r"""
    从上传的 Excel 文件导入设备资产数据。
    对应关系：
    - 设备名称 -> name
    - 设备编号 -> sn
    - 固定资产编号 -> asset_no
    - 规格型号 -> spec
    - 负责部门 -> dept
    - 设备等级 -> grade
    - ...等
    """
    import pandas as pd
    import math
    try:
        # 直接读取上传的文件流
        df = pd.read_excel(file.file)
        # 统一处理 NaN 为 None
        df = df.where(pd.notnull(df), None)
        
        def clean_str(val):
            """将 Excel 单元格值清洗为干净字符串，无效值返回 None"""
            if val is None:
                return None
            s = str(val).strip()
            # 过滤掉 pandas 遗留的 nan/None 字符串以及空串
            if s.lower() in ('nan', 'none', ''):
                return None
            # 去掉数字型字符串末尾的 .0（Excel 常见问题）
            if s.endswith('.0'):
                try:
                    float(s)
                    s = s[:-2]
                except ValueError:
                    pass
            return s

        count = 0
        skipped = 0
        errors = []
        seen_sns = set()   # 同批次 SN 去重

        for row_idx, row in df.iterrows():
            try:
                sn = clean_str(row.get('设备编号'))
                name = clean_str(row.get('设备名称'))
                
                if not sn or not name:
                    skipped += 1
                    continue

                # 同一批次 Excel 中重复 SN，只处理第一条
                if sn in seen_sns:
                    skipped += 1
                    continue
                seen_sns.add(sn)
                
                # 负责人匹配 (巡检)
                raw_inspector = row.get('责任人') or row.get('负责人') or row.get('负责人id')
                inspector_u = None
                inspector_name_clean = clean_str(raw_inspector)
                if inspector_name_clean:
                    inspector_u = db.query(User).filter((User.name == inspector_name_clean) | (User.id == inspector_name_clean)).first()
                    if not inspector_u:
                        inspector_u = User(id=f"auto_ins_{uuid.uuid4().hex[:8]}", name=inspector_name_clean, job_title="待同步检查负责人")
                        db.add(inspector_u)
                        db.flush()

                # 维修班长匹配 (维保)
                raw_leader = row.get('维修班长')
                leader_u = None
                leader_name_clean = clean_str(raw_leader)
                if leader_name_clean:
                    leader_u = db.query(User).filter((User.name == leader_name_clean) | (User.id == leader_name_clean)).first()
                    if not leader_u:
                        leader_u = User(id=f"auto_lead_{uuid.uuid4().hex[:8]}", name=leader_name_clean, job_title="待同步维修班长")
                        db.add(leader_u)
                        db.flush()

                # 检查是否已存在
                db_device = db.query(Device).filter(Device.sn == sn).first()
                if db_device:
                    db_device.name = name
                    db_device.asset_no = clean_str(row.get('固定资产编号')) or db_device.asset_no
                    db_device.spec = clean_str(row.get('规格型号')) or db_device.spec
                    db_device.manufacturer = clean_str(row.get('生产厂家')) or db_device.manufacturer
                    db_device.purchase_date = clean_str(row.get('入厂日期')) or db_device.purchase_date
                    db_device.final_inspection_date = clean_str(row.get('终验通过日期')) or db_device.final_inspection_date
                    db_device.location = clean_str(row.get('放置地点')) or db_device.location
                    db_device.useful_life = clean_str(row.get('使用年限')) or db_device.useful_life
                    db_device.usage_status = clean_str(row.get('使用状况')) or db_device.usage_status
                    db_device.dept = clean_str(row.get('负责部门')) or db_device.dept
                    db_device.grade = clean_str(row.get('设备等级')) or db_device.grade
                    db_device.maintenance_leader = leader_name_clean if leader_u else (clean_str(row.get('维修班长')) or db_device.maintenance_leader)
                    
                    if inspector_u: db_device.inspector_id = inspector_u.id
                    if leader_u: db_device.maintenance_leader_id = leader_u.id
                else:
                    device_id = str(uuid.uuid4())
                    qr_path = generate_inspection_qr_code(device_id)
                    new_dev = Device(
                        id=device_id, sn=sn, name=name, status="运行中", qr_code_path=qr_path,
                        asset_no=clean_str(row.get('固定资产编号')),
                        spec=clean_str(row.get('规格型号')),
                        manufacturer=clean_str(row.get('生产厂家')),
                        purchase_date=clean_str(row.get('入厂日期')),
                        final_inspection_date=clean_str(row.get('终验通过日期')),
                        location=clean_str(row.get('放置地点')),
                        useful_life=clean_str(row.get('使用年限')),
                        usage_status=clean_str(row.get('使用状况')),
                        dept=clean_str(row.get('负责部门')),
                        grade=clean_str(row.get('设备等级')),
                        maintenance_leader=leader_name_clean if leader_u else clean_str(row.get('维修班长')),
                        maintenance_leader_id=leader_u.id if leader_u else None,
                        inspector_id=inspector_u.id if inspector_u else None
                    )
                    db.add(new_dev)
                
                db.flush()  # 逐行 flush 以便后续行可查到已插入的 SN
                count += 1
            except Exception as row_err:
                logger.warning(f"导入第 {row_idx + 2} 行时出错(已跳过): {str(row_err)}")
                errors.append(f"第{row_idx + 2}行: {str(row_err)}")
                db.rollback()
                continue
        
        db.commit()
        result = {"message": f"成功同步 {count} 台设备资产"}
        if skipped:
            result["skipped"] = skipped
        if errors:
            result["errors"] = errors
        return result
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"导入过程中出错: {str(e)}")

@app.get("/users/", response_model=List[UserResponse], summary="拉取系统全量人员")
def list_users(db: Session = Depends(get_db)):
    """供 PC 端管理列表读取带有部门信息的检查人员表"""
    return db.query(User).options(joinedload(User.department)).all()

# ==============================================================================
# 7. 静态页面路由映射
# ==============================================================================
@app.get("/inspect/{device_id}", summary="返回检查前端页面", response_class=FileResponse)
def inspect_device_page(device_id: str):
    if not os.path.exists("inspect.html"):
        raise HTTPException(status_code=404, detail="未找到前端页面文件 inspect.html")
    return FileResponse("inspect.html")

@app.get("/login", summary="安全登录页面", response_class=FileResponse)
def login_page():
    if not os.path.exists("login.html"):
        raise HTTPException(status_code=404, detail="未找到前端页面文件 login.html")
    return FileResponse("login.html")

@app.post("/api/login", summary="管理后台登录校验 (Session/Cookie)")
def login_api(data: LoginData):
    """验证 admin 账号并颁发简单的 MVP Cookie Session"""
    if data.username == "admin" and data.password == "admin":
        response = JSONResponse(content={"message": "登录成功"})
        response.set_cookie(key="session_token", value="admin_token", httponly=True, max_age=86400)
        return response
    else:
        raise HTTPException(status_code=401, detail="管理员账号或密码错误！")

@app.get("/logout", summary="安全登出机制")
def logout():
    """清除由于登录留存的浏览器 Cookie 头，阻断下次会话并重定向至登录页"""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("session_token")
    return response

@app.get("/dashboard", summary="返回 PC 管理主界面")
def dashboard_page(request: Request):
    # 【白名单鉴权防护】
    if request.cookies.get("session_token") != "admin_token":
        # 如果未携带 admin_token 标志位，直接强行阻断并302重定向到登录页
        return RedirectResponse(url="/login", status_code=302)
        
    if not os.path.exists("dashboard.html"):
        raise HTTPException(status_code=404, detail="未找到前端页面文件 dashboard.html")
    return FileResponse("dashboard.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
@app.get("/repair/{device_id}", response_class=FileResponse)
def repair_page(device_id: str):
    return FileResponse("repair.html")

@app.get("/order/{order_id}", response_class=FileResponse)
def order_page(order_id: str):
    return FileResponse("order.html")
