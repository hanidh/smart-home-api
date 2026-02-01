#uvicorn main:app --host 127.0.0.1 --port 8000 --reload
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import psycopg2
from psycopg2.extras import RealDictCursor
import logging
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from passlib.context import CryptContext
import os
from dotenv import load_dotenv
import json
from contextlib import asynccontextmanager
import hashlib  
import secrets
from uuid import uuid4
from decimal import Decimal
from fastapi import Body
from db import db
import time
import csv
import io
import requests

# تحميل المتغيرات البيئية
load_dotenv()

# إعداد التسجيل
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# إعدادات الأمان
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# في main.py، أضف بعد السطر 52
# في main.py، أصلح دالة verify_password_fallback
def verify_password_fallback(plain_password, stored_hash):
    """محاولة تحقق متعددة من كلمة المرور"""
    try:
        # إذا كانت التجزئة تبدأ بـ $2 فهي bcrypt
        if stored_hash.startswith('$2'):
            # استخدام bcrypt مباشرةً
            import bcrypt
            try:
                return bcrypt.checkpw(
                    plain_password.encode('utf-8'), 
                    stored_hash.encode('utf-8')
                )
            except Exception as e:
                print(f"⚠️ bcrypt direct check failed: {e}")
                return False
        else:
            # خلاف ذلك، حاول باستخدام pwd_context
            return pwd_context.verify(plain_password, stored_hash)
    except Exception as e:
        print(f"❌ Error in verify_password_fallback: {e}")
        return False
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

EMQX_API = os.getenv("EMQX_API")
EMQX_APP_ID = os.getenv("EMQX_APP_ID")
EMQX_APP_SECRET = os.getenv("EMQX_APP_SECRET")


# نماذج البيانات (Pydantic)
class UserCreate(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    device_id: Optional[str] = "default"

class UserLogin(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: int
    username: str
    refresh_token: str
# # # # # # #
# بعد نموذج Token، أضف:
class TokenData(BaseModel):
    user_id: int
    username: str

class RefreshTokenRequest(BaseModel):
    refresh_token: str
class LedState(BaseModel):
    led_index: int
    is_on: bool
    desired_state: bool
    current_value: float
    current_limit: float
    device_id: Optional[str] = "default"

class MQTTMessage(BaseModel):
    topic: str
    payload: str
    qos: Optional[int] = 0
    retain: Optional[bool] = False

class DeviceCreate(BaseModel):
    device_id: str
    name: str
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    
class DeviceCommand(BaseModel):
    led_index: int
    state: bool
    current_limit: Optional[float] = None


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None

class DeviceLinkRequest(BaseModel):
    device_number: int  # الرقم فقط (xx في device_xx)

class RoomCreate(BaseModel):
    device_id: str = "default"
    name: str
    led_index: int
    initial_current_limit: float = 15.0
    icon_code: Optional[int] = None

class RoomUpdate(BaseModel):
    name: Optional[str] = None
    initial_current_limit: Optional[float] = None
    icon_code: Optional[int] = None

class TimerSettings(BaseModel):
    """إعدادات المؤقت"""
    led_index: int
    on_duration_seconds: int = 0  # ⭐⭐ قيمة افتراضية
    off_duration_seconds: int = 0  # ⭐⭐ قيمة افتراضية
    enabled: bool = False  # ⭐⭐ افتراضياً معطل

class CancelTimerRequest(BaseModel):
    led_index: int

class TimerSettingCreate(BaseModel):
    """إنشاء إعداد مؤقت"""
    led_index: int
    enabled: bool = False  # ⭐⭐ افتراضياً معطل
    is_on_phase: bool = True
    start_time: Optional[datetime] = None
    total_on_seconds: Optional[int] = 0  # ⭐⭐ قيمة افتراضية
    total_off_seconds: Optional[int] = 0  # ⭐⭐ قيمة افتراضية
    device_id: str = "default"

class TimerSettingUpdate(BaseModel):
    enabled: Optional[bool] = None
    is_on_phase: Optional[bool] = None
    start_time: Optional[datetime] = None
    total_on_seconds: Optional[int] = None
    total_off_seconds: Optional[int] = None

class DeviceScheduleRequest(BaseModel):
    """طلب جدولة الجهاز"""
    led_index: int
    start_time: str = "00:00"
    end_time: str ="00:00"   
    days: str ="1111111"       
    enabled: bool = True

class ScheduleSettingCreate(BaseModel):
    """إنشاء إعداد جدولة"""
    led_index: int
    device_id: str
    start_time: str = "00:00"
    end_time: str = "00:00"
    days: str = "1111111"

class ScheduleSettingUpdate(BaseModel):
    """تحديث إعداد جدولة"""
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    days: Optional[str] = None

class ActivityLogCreate(BaseModel):
    device_id: str = "default"
    event_type: str
    event_data: Optional[Dict[str, Any]] = None

class StatisticsRequest(BaseModel):
    device_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
class CompleteRoomData(BaseModel):
    """نموذج لبيانات الغرفة الكاملة"""
    room_id: str
    device_id: str
    name: str
    led_index: int
    initial_current_limit: float
    icon_code: Optional[int]
    created_at: str
    updated_at: str
    
    # بيانات LED
    desired_state: bool = False
    is_on: bool = False
    current_value: float = 0.0
    current_limit: float = 15.0
    
    # بيانات المؤقت
    timer_enabled: bool = False
    total_on_seconds: int = 0
    total_off_seconds: int = 0
    timer_start_time: Optional[str] = None
    
    # بيانات الجدولة
    schedule_enabled: bool = False
    schedule_start_time: Optional[str] = None
    schedule_end_time: Optional[str] = None
    schedule_days: Optional[str] = "1111111"

# 1. إضافة نموذج MainPowerUpdate
class MainPowerUpdate(BaseModel):
    main_power: bool

# 2. إضافة نموذج MainPowerCommand
class MainPowerCommand(BaseModel):
    state: bool  # true=تشغيل, false=إيقاف

# دوال المساعدة
def device_topic(device_id: str, suffix: str):
    return f"device/{device_id}/{suffix}"

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt
def create_tokens(data: dict):
    """إنشاء Access Token و Refresh Token معاً"""
    # Access Token (قصير العمر)
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(data, access_token_expires)
    
    # Refresh Token (طويل العمر) - 30 يوم
    refresh_token_expires = timedelta(days=30)
    refresh_data = data.copy()
    refresh_data.update({"type": "refresh"})
    refresh_token = create_access_token(refresh_data, refresh_token_expires)
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60
    }
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("user_id")
        username: str = payload.get("username")
        print("🟢 get_current_user called")

        if user_id is None or username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials"
            )
        return {"user_id": user_id, "username": username}
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials"
        )

# دوال قاعدة البيانات
def get_user_by_username(username: str):
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        return user
    except Exception as e:
        logger.error(f"Error getting user: {e}")
        return None
    finally:
        cursor.close()

def create_user(user: UserCreate):
    """إصدار مبسط من create_user بدون إنشاء LED"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        print(f"\n📝 إنشاء مستخدم (نسخة مبسطة)")
        print(f"👤 Username: {user.username}")
        
        # التحقق من وجود المستخدم
        existing_user = get_user_by_username(user.username)
        if existing_user:
            raise HTTPException(status_code=400, detail="Username already exists")
        
        # تقليم كلمة المرور إذا كانت طويلة
        if len(user.password) > 72:
            user.password = user.password[:72]
        
        # استخدام bcrypt مباشرة
        import bcrypt
        salt = bcrypt.gensalt(rounds=12)
        hashed_password = bcrypt.hashpw(user.password.encode('utf-8'), salt).decode('utf-8')
        
        print(f"🔐 التجزئة: {hashed_password[:30]}...")
        
        # إدراج المستخدم فقط
        cursor.execute(
            "INSERT INTO users (username, password, email, device_id) VALUES (%s, %s, %s, %s) RETURNING id",
            (user.username, hashed_password, user.email, user.device_id)
        )
        user_id = cursor.fetchone()[0]
        
        connection.commit()
        print(f"✅ تم إنشاء المستخدم (ID: {user_id})")
        return user_id
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ خطأ في إنشاء المستخدم: {e}")
        if connection:
            connection.rollback()
        raise HTTPException(status_code=500, detail="Failed to create user")
    finally:
        if cursor:
            cursor.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Application startup")
    yield  # Add this yield statement
    logger.info("🛑 Application shutdown")

# تعريف التطبيق مرة واحدة فقط
app = FastAPI(
    title="Smart Home API", 
    version="1.0.0",
    lifespan=lifespan,
    redirect_slashes=False
)
app.router.lifespan_context = lifespan

# إعدادات CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# نقاط النهاية (Endpoints)
@app.get("/")
async def root():
    return {"message": "Smart Home API", "status": "running", "timestamp": datetime.now().isoformat()}

@app.post("/api/auth/register", response_model=dict)
async def register(user: UserCreate):
    user_id = create_user(user)
    # ربط هوية MQTT بالمستخدم
    mqtt_username, mqtt_password = assign_mqtt_identity_to_user(user_id)
    if mqtt_username and mqtt_password:
        return {
            "message": "User created successfully", 
            "user_id": user_id, 
            "mqtt_assigned": True,
            "mqtt_username": mqtt_username
        }
    else:
        return {
            "message": "User created but no MQTT identity available", 
            "user_id": user_id, 
            "mqtt_assigned": False
        }
@app.post("/api/auth/login", response_model=Token)
async def login(user: UserLogin):
    print(f"\n🔐 ========== محاولة تسجيل دخول ==========")
    print(f"📝 المستخدم: {user.username}")
    print(f"🔑 كلمة المرور المدخلة: {user.password}")
    
    # الحصول على المستخدم من قاعدة البيانات
    db_user = get_user_by_username(user.username)
    if not db_user:
        print(f"❌ المستخدم غير موجود في قاعدة البيانات!")
        raise HTTPException(status_code=400, detail="Invalid username or password")
    
    print(f"✅ المستخدم موجود في قاعدة البيانات")
    print(f"📊 بيانات المستخدم: ID={db_user['id']}, Email={db_user.get('email')}")
    
    # التحقق من كلمة المرور
    stored_hash = db_user["password"]
    if not verify_password_fallback(user.password, stored_hash):
        print(f"❌ فشل التحقق من كلمة المرور!")
        raise HTTPException(status_code=400, detail="Invalid username or password")
    
    print(f"🎉 تسجيل دخول ناجح!")
    
    # التحقق من وجود هوية MQTT للمستخدم
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT username, password 
        FROM mqtt_identities 
        WHERE identity_type = 'user' AND u_id = %s
        LIMIT 1
    """, (db_user["id"],))
    identity = cursor.fetchone()
    
    if not identity:
        print("⚠️ لا توجد هوية MQTT للمستخدم، جاري تعيين واحدة...")
        mqtt_username, mqtt_password = assign_mqtt_identity_to_user(db_user["id"])
        if mqtt_username and mqtt_password:
            identity = {"username": mqtt_username, "password": mqtt_password}
            print(f"✅ تم تعيين هوية MQTT: {mqtt_username}")
        else:
            print("❌ لا توجد هويات MQTT متاحة!")
            raise HTTPException(status_code=500, detail="No MQTT identity available")
    
    cursor.close()
    connection.close()
    
    # إنشاء التوكنين
    tokens = create_tokens({"user_id": db_user["id"], "username": db_user["username"]})
    
    # حفظ Refresh Token في قاعدة البيانات
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("""
            INSERT INTO refresh_tokens (user_id, token, expires_at) 
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE 
            SET token = EXCLUDED.token, expires_at = EXCLUDED.expires_at
        """, (
            db_user["id"],
            tokens["refresh_token"],
            datetime.utcnow() + timedelta(days=30)
        ))
        connection.commit()
    finally:
        cursor.close()
    response_data = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],  # ⭐ تأكد من وجوده
        "token_type": "bearer",
        "user_id": db_user["id"],
        "username": db_user["username"]
    }
    print(f"📤 البيانات المرسلة: {response_data}")
    
    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],  # ⭐ تأكد من وجوده
        "token_type": "bearer",
        "user_id": db_user["id"],
        "username": db_user["username"]
    }
@app.post("/api/auth/refresh", response_model=dict)
async def refresh_token(request: RefreshTokenRequest):
    """تجديد Access Token باستخدام Refresh Token"""
    try:
        # فك تشفير Refresh Token
        payload = jwt.decode(
            request.refresh_token, 
            SECRET_KEY, 
            algorithms=[ALGORITHM]
        )
        
        # التحقق من نوع التوكن
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        
        user_id = payload.get("user_id")
        username = payload.get("username")
        
        if not user_id or not username:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        # التحقق من وجود التوكن في قاعدة البيانات
        connection = db.get_connection()
        cursor = connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT token, expires_at FROM refresh_tokens 
            WHERE user_id = %s AND token = %s
        """, (user_id, request.refresh_token))
        
        token_record = cursor.fetchone()
        cursor.close()
        
        if not token_record:
            raise HTTPException(status_code=401, detail="Token not found")
        
        # التحقق من انتهاء الصلاحية
        expires_at = token_record["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        
        if expires_at < datetime.utcnow():
            raise HTTPException(status_code=401, detail="Token expired")
        
        # إنشاء Access Token جديد
        tokens = create_tokens({"user_id": user_id, "username": username})
        
        # تحديث Refresh Token في قاعدة البيانات
        cursor = connection.cursor()
        cursor.execute("""
            UPDATE refresh_tokens 
            SET token = %s, expires_at = %s
            WHERE user_id = %s
        """, (
            tokens["refresh_token"],
            datetime.utcnow() + timedelta(days=30),
            user_id
        ))
        connection.commit()
        cursor.close()
        
        return {
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "token_type": "bearer",
            "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60
        }
        
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
@app.post("/api/auth/logout", response_model=dict)
async def logout(
    current_user: dict = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """تسجيل الخروج وحذف Refresh Token"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        # حذف Refresh Token
        cursor.execute("DELETE FROM refresh_tokens WHERE user_id = %s", 
                      (current_user["user_id"],))
        connection.commit()
        return {"message": "Logged out successfully"}
    finally:
        cursor.close()
def update_user_password(user_id: int, new_password: str):
    """تحديث كلمة مرور المستخدم إلى التشفير الصحيح"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        hashed_password = get_password_hash(new_password)
        cursor.execute(
            "UPDATE users SET password = %s WHERE id = %s",
            (hashed_password, user_id)
        )
        connection.commit()
        logger.info(f"Updated password for user {user_id}")
    except Exception as e:
        logger.error(f"Error updating password: {e}")
        connection.rollback()
    finally:
        cursor.close()

def get_device_by_id(device_id: str):
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM devices WHERE device_id = %s", (device_id,))
        device = cursor.fetchone()
        return device
    except Exception as e:
        logger.error(f"Error getting device: {e}")
        return None
    finally:
        cursor.close()

def get_room_by_id(room_id: str):
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM rooms WHERE room_id = %s", (room_id,))
        room = cursor.fetchone()
        return room
    except Exception as e:
        logger.error(f"Error getting room: {e}")
        return None
    finally:
        cursor.close()
# 3. دالة لتحديث حالة القاطع في قاعدة البيانات
def update_device_main_power(device_id: str, main_power: bool):
    """تحديث حالة القاطع الرئيسي للجهاز"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("""
            UPDATE devices 
            SET main_power = %s, updated_at = CURRENT_TIMESTAMP
            WHERE device_id = %s
        """, (main_power, device_id))
        connection.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating main power: {e}")
        connection.rollback()
        return False
    finally:
        cursor.close()

# 4. نقطة نهاية لجلب حالة القاطع
@app.get("/api/devices/{device_id}/main-power", response_model=Dict)
async def get_device_main_power(
    device_id: str,
    current_user: dict = Depends(get_current_user)
):
    """الحصول على حالة القاطع الرئيسي للجهاز"""
    
    # التحقق من أن الجهاز يخص المستخدم
    device = get_device_by_id(device_id)
    if not device or device["u_id"] != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="Not your device")
    
    return {
        "device_id": device_id,
        "main_power": device.get("main_power", False)
    }

# 5. نقطة نهاية لتحديث حالة القاطع
@app.put("/api/devices/{device_id}/main-power", response_model=dict)
async def update_device_main_power_api(
    device_id: str,
    main_power_update: MainPowerUpdate,
    current_user: dict = Depends(get_current_user)
):
    """تحديث حالة القاطع الرئيسي للجهاز"""
    
    # التحقق من أن الجهاز يخص المستخدم
    device = get_device_by_id(device_id)
    if not device or device["u_id"] != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="Not your device")
    
    connection = db.get_connection()
    cursor = connection.cursor()
    
    try:
        # تحديث حالة القاطع الرئيسي في قاعدة البيانات
        cursor.execute("""
            UPDATE devices 
            SET main_power = %s, updated_at = CURRENT_TIMESTAMP
            WHERE device_id = %s
        """, (main_power_update.main_power, device_id))
        
        connection.commit()
        
        # تسجيل النشاط
        cursor.execute("""
            INSERT INTO activity_logs (device_id, event_type, event_data)
            VALUES (%s, %s, %s)
        """, (
            device_id,
            "main_power_update",
            json.dumps({
                "main_power": main_power_update.main_power,
                "by_user": current_user["username"]
            })
        ))
        
        connection.commit()
        
        return {
            "message": "Main power updated successfully",
            "main_power": main_power_update.main_power
        }
    
    except Exception as e:
        logger.error(f"Error updating main power: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail="Failed to update main power")
    finally:
        cursor.close()


def get_timer_setting(led_index: int, device_id: str):
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            """
            SELECT * FROM timer_settings 
            WHERE led_index = %s AND device_id = %s
            """,
            (led_index, device_id)
        )
        timer = cursor.fetchone()
        return timer
    except Exception as e:
        logger.error(f"Error getting timer setting: {e}")
        return None
    finally:
        cursor.close()

def get_schedule_setting(led_index: int, device_id: str):
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            "SELECT * FROM schedule_settings WHERE led_index = %s AND device_id = %s",
            (led_index, device_id)
        )
        schedule = cursor.fetchone()
        return schedule
    except Exception as e:
        logger.error(f"Error getting schedule setting: {e}")
        return None
    finally:
        cursor.close()

def calculate_timer_progress(timer_setting):
    """حساب التقدم الحالي للمؤقت - الإصدار المحسن"""
    # ⭐⭐ التحقق من وجود بيانات المؤقت
    if not timer_setting:
        print("⚠️ لا يوجد إعدادات مؤقت")
        return None
    
    total_on = timer_setting.get('total_on_seconds', 0)
    total_off = timer_setting.get('total_off_seconds', 0)
    
    # ⭐⭐ الشرطان الأساسيان للمؤقت:
    # 1. وجود مدد زمنية > 0
    # 2. enabled = True في قاعدة البيانات (سيتم تحديثها تلقائياً)
    
    if total_on <= 0 or total_off <= 0:
        print(f"⚠️ المدد غير صالحة: on={total_on}, off={total_off}")
        return None
    
    now = datetime.now()
    start_time = timer_setting['start_time']
    
    # تحويل start_time من string إلى datetime إذا لزم
    if isinstance(start_time, str):
        start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
    
    total_seconds = total_on + total_off
    
    if not start_time or total_seconds == 0:
        print("⚠️ لا يوجد start_time أو total_seconds = 0")
        return None
    
    elapsed = (now - start_time).total_seconds()
    cycle_position = elapsed % total_seconds
    
    # حساب حالة المؤقت الحالية
    is_on_phase = timer_setting.get('is_on_phase', True)
    
    if is_on_phase:
        is_on = cycle_position < total_on
        if is_on:
            remaining = total_on - cycle_position
            progress = remaining / total_on if total_on > 0 else 0
        else:
            remaining = total_seconds - cycle_position
            progress = remaining / total_off if total_off > 0 else 0
    else:
        is_on = cycle_position >= total_off
        if not is_on:
            remaining = total_off - cycle_position
            progress = remaining / total_off if total_off > 0 else 0
        else:
            remaining = total_seconds - cycle_position
            progress = remaining / total_on if total_on > 0 else 0
    
    result = {
        'is_on': is_on,
        'remaining_seconds': int(remaining),
        'progress': progress,
        'cycle_position': cycle_position
    }
    
    return result

@app.get("/api/led-states", response_model=List[Dict])
async def get_led_states(device_id: Optional[str] = "default"):
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        # تحقق من صلاحية المستخدم للجهاز
        cursor.execute("""
            SELECT * FROM led_states 
            WHERE device_id = %s 
            ORDER BY led_index
        """, (device_id,))
        led_states = cursor.fetchall()
        
        # تحويل Decimal إلى float لتجنب مشاكل JSON
        for state in led_states:
            for key, value in state.items():
                if isinstance(value, Decimal):
                    state[key] = float(value)
                    
        return led_states
    except Exception as e:
        logger.error(f"Error getting LED states: {e}")
        raise HTTPException(status_code=500, detail="Failed to get LED states")
    finally:
        cursor.close()

@app.put("/api/led-states/{led_index}", response_model=dict)
async def update_led_state(
    led_index: int,
    led_state: LedState,
    current_user: dict = Depends(get_current_user)
):
    logger.info(f"🔄 تحديث LED {led_index} للجهاز: {led_state.device_id}")
    logger.info(f"📊 بيانات LED: {led_state.dict()}")
    
    if not led_state.device_id or led_state.device_id == "default":
        # ⭐ إذا كان device_id فارغاً أو "default"، استخدم الجهاز الافتراضي للمستخدم
        led_state.device_id = "default"
        logger.info(f"⚠️ استخدام الجهاز الافتراضي للمستخدم: {current_user['username']}")
    
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        # ⭐⭐ تحقق أولاً من وجود الجهاز في قاعدة البيانات
        cursor.execute(
            "SELECT device_id FROM devices WHERE device_id = %s",
            (led_state.device_id,)
        )
        device_exists = cursor.fetchone()
        
        if not device_exists:
            logger.warning(f"⚠️ الجهاز {led_state.device_id} غير موجود، سيتم إنشاء سجل LED له")
        
        cursor.execute("""
            SELECT 1 FROM led_states
            WHERE led_index = %s AND device_id = %s
        """, (led_index, led_state.device_id))

        if not cursor.fetchone():
            raise HTTPException(
                status_code=404,
                detail="LED state not found. Room must exist first."
            )

        cursor.execute("""
            UPDATE led_states
            SET
                is_on = %s,
                desired_state = %s,
                current_value = %s,
                current_limit = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE led_index = %s AND device_id = %s
        """, (
            led_state.is_on,
            led_state.desired_state,
            led_state.current_value,
            led_state.current_limit,
            led_index,
            led_state.device_id
        ))

        
        connection.commit()
        
        # تسجيل النشاط
        cursor.execute("""
            INSERT INTO activity_logs (device_id, event_type, event_data)
            VALUES (%s, %s, %s)
        """, (
            led_state.device_id,
            "led_state_update",
            json.dumps({
                "led_index": led_index,
                "is_on": led_state.is_on,
                "desired_state": led_state.desired_state,
                "current_value": led_state.current_value,
                "current_limit": led_state.current_limit,
                "updated_by": current_user["username"]
            })
        ))
        
        connection.commit()
        
        logger.info(f"✅ تم تحديث LED {led_index} للجهاز {led_state.device_id}")
        return {"message": f"LED {led_index} updated successfully"}
    except Exception as e:
        logger.error(f"❌ خطأ في تحديث حالة LED: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update LED state: {str(e)}")
    finally:
        cursor.close()

@app.post("/api/mqtt/save", response_model=dict)
async def save_mqtt_message(mqtt_msg: MQTTMessage):
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("""
            INSERT INTO mqtt_messages (topic, payload, qos, retain, arrived)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            mqtt_msg.topic,
            mqtt_msg.payload,
            mqtt_msg.qos,
            mqtt_msg.retain,
            datetime.now()
        ))
        
        connection.commit()
        return {"message": "MQTT message saved successfully"}
    except Exception as e:
        logger.error(f"Error saving MQTT message: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail="Failed to save MQTT message")
    finally:
        cursor.close()

@app.get("/api/mqtt/messages", response_model=List[Dict])
async def get_mqtt_messages(
    topic: Optional[str] = None,
    limit: int = 100,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        query = "SELECT * FROM mqtt_messages WHERE 1=1"
        params = []
        
        if topic:
            query += " AND topic = %s"
            params.append(topic)
        
        if start_date:
            query += " AND arrived >= %s"
            params.append(start_date)
        
        if end_date:
            query += " AND arrived <= %s"
            params.append(end_date)
        
        query += " ORDER BY arrived DESC LIMIT %s"
        params.append(limit)
        
        cursor.execute(query, params)
        messages = cursor.fetchall()
        return messages
    except Exception as e:
        logger.error(f"Error getting MQTT messages: {e}")
        raise HTTPException(status_code=500, detail="Failed to get MQTT messages")
    finally:
        cursor.close()
@app.get("/api/mqtt/credentials", response_model=dict)
async def get_mqtt_credentials(
    current_user: dict = Depends(get_current_user)
):
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)

    try:
        user_id = current_user["user_id"]

        cursor.execute("""
            SELECT username, password
            FROM mqtt_identities
            WHERE identity_type = 'user'
              AND u_id = %s
            LIMIT 1
        """, (user_id,))
        identity = cursor.fetchone()

        if not identity:
            raise HTTPException(
                status_code=404,
                detail="MQTT credentials not provisioned. Contact admin."
            )

        return {
            "mqtt": {
                "host": "k117111f.ala.us-east-1.emqxsl.com",
                "port": 8883,
                "username": identity["username"],
                "password": identity["password"]
            }
        }

    finally:
        cursor.close()
        connection.close()

@app.get("/api/activity/logs", response_model=List[Dict])
async def get_activity_logs(
    device_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 100
):
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        query = "SELECT * FROM activity_logs WHERE 1=1"
        params = []
        
        if device_id:
            query += " AND device_id = %s"
            params.append(device_id)
        
        if event_type:
            query += " AND event_type = %s"
            params.append(event_type)
        
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        
        cursor.execute(query, params)
        logs = cursor.fetchall()
        return logs
    except Exception as e:
        logger.error(f"Error getting activity logs: {e}")
        raise HTTPException(status_code=500, detail="Failed to get activity logs")
    finally:
        cursor.close()

# إحصائيات النظام
@app.get("/api/stats", response_model=Dict)
async def get_system_stats():
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        stats = {}
        
        # عدد رسائل MQTT
        cursor.execute("SELECT COUNT(*) as count FROM mqtt_messages")
        stats["mqtt_messages_count"] = cursor.fetchone()["count"]
        
        # عدد سجلات النشاط
        cursor.execute("SELECT COUNT(*) as count FROM activity_logs")
        stats["activity_logs_count"] = cursor.fetchone()["count"]
        
        # عدد المستخدمين
        cursor.execute("SELECT COUNT(*) as count FROM users")
        stats["users_count"] = cursor.fetchone()["count"]
        
        # حالات المصابيح
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN is_on THEN 1 ELSE 0 END) as on_count,
                SUM(CASE WHEN NOT is_on THEN 1 ELSE 0 END) as off_count
            FROM led_states
        """)
        led_stats = cursor.fetchone()
        stats["led_stats"] = led_stats
        
        # آخر رسالة MQTT
        cursor.execute("SELECT topic, payload, arrived FROM mqtt_messages ORDER BY arrived DESC LIMIT 1")
        stats["last_mqtt_message"] = cursor.fetchone()
        
        return stats
    except Exception as e:
        logger.error(f"Error getting system stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get system stats")
    finally:
        cursor.close()
# ... بعد نقطة نهاية /api/stats، أضف:

# === إدارة الأجهزة ===
@app.post("/api/devices", response_model=dict)
async def create_device(device: DeviceCreate, current_user: dict = Depends(get_current_user)):
    """إنشاء جهاز جديد"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        # التحقق من وجود الجهاز
        existing_device = get_device_by_id(device.device_id)
        if existing_device:
            raise HTTPException(status_code=400, detail="Device ID already exists")
        
        cursor.execute("""
            INSERT INTO devices (device_id, name, host, port, username, password, u_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            device.device_id,
            device.name,
            device.host,
            device.port,
            device.username,
            device.password,
            current_user["user_id"]   # ⭐ هذا هو المفتاح
        ))

        mqtt_username = f"dev_{device.device_id}"
        mqtt_password = secrets.token_urlsafe(16)
        cursor.execute("""
            INSERT INTO mqtt_identities (username, password, identity_type, device_id,u_id,main_power)
            VALUES (%s, %s, 'device', %s,%s,false)
        """, (
            mqtt_username,
            mqtt_password,
            device.device_id,
            current_user["user_id"]
        ))

        connection.commit()
        
        # تسجيل النشاط
        cursor.execute("""
            INSERT INTO activity_logs (device_id, event_type, event_data)
            VALUES (%s, %s, %s)
        """, (
            device.device_id,
            "device_created",
            json.dumps({
                "device_id": device.device_id,
                "name": device.name,
                "created_by": current_user["username"]
            })
        ))
        
        connection.commit()

        return {
            "message": "Device created successfully",
            "device_id": device.device_id,
            "mqtt": {
                "host": "y0a0109e.ala.us-east-1.aws.emqxtables.com",
                "port": 8883,
                "username": mqtt_username,
                "password": mqtt_password
            }
        }   
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating device: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail="Failed to create device")
    finally:
        cursor.close()

@app.get("/api/devices", response_model=List[Dict])
async def get_devices(current_user: dict = Depends(get_current_user)):
    """جلب أجهزة المستخدم الحالي - إصدار شامل"""
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        user_id = current_user["user_id"]
        logger.info(f"🔍 جلب أجهزة للمستخدم ID: {user_id}")
        
        # ⭐⭐ **الحل الشامل: استخدام UNION لدمج نتائج من مصدرين**
        cursor.execute("""
            -- الأجهزة من جدول devices مباشرة
            SELECT 
                d.*,
                'owner' as role,
                mi.username as mqtt_username
            FROM devices d
            LEFT JOIN mqtt_identities mi ON d.device_id = mi.device_id
            WHERE d.u_id = %s
            
            UNION
            
            -- الأجهزة من جدول user_devices (للتوافق مع البيانات القديمة)
            SELECT 
                d.*,
                ud.role,
                mi.username as mqtt_username
            FROM user_devices ud
            JOIN devices d ON ud.device_id = d.device_id
            LEFT JOIN mqtt_identities mi ON d.device_id = mi.device_id
            WHERE ud.user_id = %s
            
            ORDER BY created_at DESC
        """, (user_id, user_id))
        
        devices = cursor.fetchall()
        logger.info(f"✅ تم جلب {len(devices)} جهاز للمستخدم {user_id}")
        
        # تحويل Decimal إلى float
        for device in devices:
            for key, value in device.items():
                if isinstance(value, Decimal):
                    device[key] = float(value)
        
        return devices
    except Exception as e:
        logger.error(f"❌ خطأ في جلب الأجهزة: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get devices")
    finally:
        cursor.close()

@app.get("/api/devices/available", response_model=List[Dict])
async def get_available_devices(current_user: dict = Depends(get_current_user)):
    """الحصول على قائمة الأجهزة المتاحة للربط"""
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT 
                username as device_id,
                CASE 
                    WHEN u_id IS NULL THEN 'متاح'
                    ELSE 'مربوط'
                END as status,
                u_id as assigned_to
            FROM mqtt_identities 
            WHERE identity_type = 'device'
            ORDER BY username
        """)
        
        devices = cursor.fetchall()
        return devices
    except Exception as e:
        logger.error(f"❌ خطأ في جلب الأجهزة المتاحة: {e}")
        raise HTTPException(status_code=500, detail="Failed to get available devices")
    finally:
        cursor.close()
@app.post("/api/devices/verify", response_model=dict)###new
async def api_verify_device(device_id: int, current_user: dict = Depends(get_current_user)):
    """
    التحقق من وجود الجهاز وربطه بالمستخدم
    """
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        user_id = current_user["user_id"]
        if not verify_user_device(user_id, device_id, cursor):
            raise HTTPException(status_code=404, detail="Device not found or not linked to user")
        
        # لو موجود، نعيد تفاصيل للتطبيق
        cursor.execute("SELECT id, name FROM devices WHERE id = %s", (device_id,))
        device = cursor.fetchone()
        return {
            "device_id": device["id"],
            "name": device["name"],
            "mqtt_topic_base": f"home/{device['id']}"
        }

    finally:
        cursor.close()
        connection.close()
@app.get("/api/devices") ###new
async def get_user_devices(current_user: dict = Depends(get_current_user)):
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        user_id = current_user["user_id"]

        cursor.execute("""
            SELECT d.id, d.name
            FROM devices d
            JOIN user_devices ud ON ud.device_id = d.id
            WHERE ud.user_id = %s AND ud.status = 'active'
        """, (user_id,))

        devices = cursor.fetchall()

        return {
            "devices": [
                {
                    "device_id": d["id"],
                    "name": d["name"],
                    "mqtt_topic_base": f"home/{d['id']}"
                }
                for d in devices
            ]
        }

    finally:
        cursor.close()
        connection.close()
@app.post("/api/devices/verify-link", response_model=dict)
async def verify_and_link_device(
    request: DeviceLinkRequest,
    current_user: dict = Depends(get_current_user)
):
    """التحقق من رقم الجهاز وربطه بالمستخدم (متعدد المستخدمين)"""
    
    device_id = f"device_{request.device_number}"
    
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    
    try:
        print(f"🔍 التحقق من الجهاز: {device_id} للمستخدم: {current_user['user_id']}")
        
        # 1. التحقق من وجود الجهاز في جدول mqtt_identities
        cursor.execute("""
            SELECT id, username, u_id, identity_type
            FROM mqtt_identities 
            WHERE username = %s AND identity_type = 'device'
        """, (device_id,))
        
        device_identity = cursor.fetchone()
        
        if not device_identity:
            print(f"❌ الجهاز {device_id} غير موجود في mqtt_identities")
            raise HTTPException(
                status_code=404, 
                detail="الجهاز غير موجود في قاعدة البيانات"
            )
        
        print(f"✅ تم العثور على الجهاز في mqtt_identities: {device_identity}")
        
        # 2. جلب معلومات المستخدم المرتبط الحالي (إن وجد)
        linked_user_id = device_identity['u_id']
        linked_username = None
        already_linked = False
        
        if linked_user_id is not None:
            # جلب اسم المستخدم المرتبط
            cursor.execute("SELECT username FROM users WHERE id = %s", (linked_user_id,))
            linked_user = cursor.fetchone()
            if linked_user:
                linked_username = linked_user['username']
            
            # التحقق إذا كان المستخدم الحالي مرتبطاً بالفعل
            cursor.execute("""
                SELECT 1 FROM user_devices 
                WHERE user_id = %s AND device_id = %s
            """, (current_user["user_id"], device_id))
            already_linked = cursor.fetchone() is not None
        
        # 3. إذا كان الجهاز مرتبطاً بمستخدم آخر، نرجع معلومات المستخدم الآخر
        if linked_user_id is not None and linked_user_id != current_user["user_id"]:
            print(f"⚠️ الجهاز مرتبط بمستخدم آخر: {linked_username} (ID: {linked_user_id})")
            
            return {
                "message": "الجهاز مرتبط بحساب آخر",
                "device_id": device_id,
                "linked_to_other_user": True,
                "other_user_id": linked_user_id,
                "other_username": linked_username,
                "already_linked_by_current_user": already_linked,
                "status": "linked_to_other"
            }
        
        # 4. إذا كان الجهاز مرتبطاً بالفعل بنفس المستخدم
        if linked_user_id == current_user["user_id"]:
            print(f"✅ الجهاز مرتبط بالفعل بهذا المستخدم")
            
            # التحقق من وجود السجل في user_devices
            if not already_linked:
                # إضافة السجل في user_devices
                cursor.execute("""
                    INSERT INTO user_devices (user_id, device_id, role)
                    VALUES (%s, %s, 'owner')
                """, (current_user["user_id"], device_id))
            
            return {
                "message": "الجهاز مرتبط بالفعل بك",
                "device_id": device_id,
                "linked_to_other_user": False,
                "already_linked_by_current_user": True,
                "status": "already_owned"
            }
        
        # 5. إذا لم يكن الجهاز مرتبطاً بأي مستخدم، نربطه بالمستخدم الحالي
        print(f"🔗 ربط الجهاز {device_id} بالمستخدم {current_user['user_id']}")
        
        # تحديث mqtt_identities لتعيين المالك الرئيسي
        cursor.execute("""
            UPDATE mqtt_identities 
            SET u_id = %s
            WHERE username = %s
        """, (current_user["user_id"], device_id))
        
        # إضافة السجل في user_devices
        cursor.execute("""
            INSERT INTO user_devices (user_id, device_id, role)
            VALUES (%s, %s, 'owner')
        """, (current_user["user_id"], device_id))
        
        # إضافة/تحديث الجهاز في جدول devices
        cursor.execute("""
            INSERT INTO devices (device_id, name, u_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (device_id) DO UPDATE 
            SET u_id = EXCLUDED.u_id, updated_at = CURRENT_TIMESTAMP
        """, (
            device_id, 
            f"جهاز {request.device_number}", 
            current_user["user_id"]
        ))
        
        connection.commit()
        print(f"✅ تم ربط الجهاز في قاعدة البيانات")
        
        # تسجيل النشاط
        cursor.execute("""
            INSERT INTO activity_logs (device_id, event_type, event_data)
            VALUES (%s, %s, %s)
        """, (
            device_id,
            "device_linked",
            json.dumps({
                "device_id": device_id,
                "device_number": request.device_number,
                "linked_by": current_user["username"],
                "is_primary_owner": True
            })
        ))
        
        connection.commit()
        print(f"✅ تم تسجيل النشاط")
        
        return {
            "message": "تم ربط الجهاز بنجاح",
            "device_id": device_id,
            "device_number": request.device_number,
            "linked_to_other_user": False,
            "already_linked_by_current_user": True,
            "status": "linked_successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ خطأ في ربط الجهاز: {e}", exc_info=True)
        connection.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to link device: {str(e)}")
    finally:
        cursor.close()###############################
@app.post("/api/devices/{device_id}/add-user", response_model=dict)
async def add_user_to_device(
    device_id: str,
    current_user: dict = Depends(get_current_user)
):
    """إضافة المستخدم الحالي كمسؤول إضافي للجهاز"""
    
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    
    try:
        print(f"👥 إضافة المستخدم {current_user['username']} للجهاز {device_id}")
        
        # 1. التحقق من وجود الجهاز
        cursor.execute("""
            SELECT device_id, u_id FROM devices 
            WHERE device_id = %s
        """, (device_id,))
        
        device = cursor.fetchone()
        if not device:
            raise HTTPException(
                status_code=404, 
                detail="الجهاز غير موجود"
            )
        
        # 2. جلب المالك الرئيسي
        primary_owner_id = device['u_id']
        cursor.execute("SELECT username FROM users WHERE id = %s", (primary_owner_id,))
        primary_owner = cursor.fetchone()
        primary_owner_name = primary_owner['username'] if primary_owner else "غير معروف"
        
        # 3. التحقق إذا كان المستخدم الحالي مرتبطاً بالفعل
        cursor.execute("""
            SELECT role FROM user_devices 
            WHERE user_id = %s AND device_id = %s
        """, (current_user["user_id"], device_id))
        
        existing_link = cursor.fetchone()
        
        if existing_link:
            return {
                "message": f"أنت مرتبط بالفعل بهذا الجهاز كـ {existing_link['role']}",
                "device_id": device_id,
                "role": existing_link['role'],
                "already_linked": True
            }
        
        # 4. إضافة المستخدم كمسؤول إضافي
        cursor.execute("""
            INSERT INTO user_devices (user_id, device_id, role)
            VALUES (%s, %s, 'admin')
        """, (current_user["user_id"], device_id))
        
        connection.commit()
        
        # 5. تسجيل النشاط
        cursor.execute("""
            INSERT INTO activity_logs (device_id, event_type, event_data)
            VALUES (%s, %s, %s)
        """, (
            device_id,
            "device_user_added",
            json.dumps({
                "added_user_id": current_user["user_id"],
                "added_username": current_user["username"],
                "added_by": current_user["username"],  # ذاتي
                "primary_owner": primary_owner_name,
                "role_assigned": "admin"
            })
        ))
        
        connection.commit()
        
        return {
            "message": f"تمت إضافتك إلى الجهاز بنجاح. المالك الرئيسي: {primary_owner_name}",
            "device_id": device_id,
            "primary_owner": primary_owner_name,
            "role": "admin",
            "already_linked": False
        }
        
    except Exception as e:
        logger.error(f"❌ خطأ في إضافة المستخدم: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail="فشل إضافة المستخدم للجهاز")
    finally:
        cursor.close()
def assign_mqtt_identity_to_user(user_id: int):
    """ربط هوية MQTT غير مستخدمة بالمستخدم"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        # البحث عن هوية مستخدم غير مرتبطة (u_id هو null)
        cursor.execute("""
            SELECT username, password 
            FROM mqtt_identities 
            WHERE identity_type = 'user' AND u_id IS NULL 
            LIMIT 1
        """)
        identity = cursor.fetchone()
        
        if identity:
            username, password = identity
            # ربط الهوية بالمستخدم
            cursor.execute("""
                UPDATE mqtt_identities 
                SET u_id = %s 
                WHERE username = %s
            """, (user_id, username))
            connection.commit()
            logger.info(f"Assigned MQTT identity {username} to user {user_id}")
            return username, password
        else:
            logger.error("No available MQTT identities for users")
            return None, None
    except Exception as e:
        logger.error(f"Error assigning MQTT identity: {e}")
        connection.rollback()
        return None, None
    finally:
        cursor.close()
@app.get("/api/devices/{device_id}", response_model=Dict)
async def get_device_by_id_api(
    device_id: str,
    current_user: dict = Depends(get_current_user)
):
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT *
            FROM devices
            WHERE device_id = %s AND u_id = %s
        """, (device_id, current_user["user_id"]))

        device = cursor.fetchone()
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        return device
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting device: {e}")
        raise HTTPException(status_code=500, detail="Failed to get device")
    finally:
        cursor.close()

@app.put("/api/devices/{device_id}", response_model=dict)
async def update_device(
    device_id: str, 
    device_update: DeviceUpdate,
    current_user: dict = Depends(get_current_user)
):
    """تحديث بيانات جهاز"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        # التحقق من وجود الجهاز
        existing_device = get_device_by_id(device_id)
        if not existing_device:
            raise HTTPException(status_code=404, detail="Device not found")
        cursor.execute("""
            SELECT id FROM devices
            WHERE device_id = %s AND u_id = %s
        """, (device_id, current_user["user_id"]))

        if not cursor.fetchone():
            raise HTTPException(status_code=403, detail="Not your device")

        # بناء استعلام التحديث الديناميكي
        update_fields = []
        update_values = []
        
        if device_update.name is not None:
            update_fields.append("name = %s")
            update_values.append(device_update.name)
        if device_update.host is not None:
            update_fields.append("host = %s")
            update_values.append(device_update.host)
        if device_update.port is not None:
            update_fields.append("port = %s")
            update_values.append(device_update.port)
        if device_update.username is not None:
            update_fields.append("username = %s")
            update_values.append(device_update.username)
        if device_update.password is not None:
            update_fields.append("password = %s")
            update_values.append(device_update.password)
        
        if not update_fields:
            return {"message": "No fields to update"}
        
        update_values.append(device_id)
        
        query = f"""
            UPDATE devices 
            SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP
            WHERE device_id = %s
            RETURNING id
        """
        
        cursor.execute(query, tuple(update_values))
        
        connection.commit()
        
        # تسجيل النشاط
        cursor.execute("""
            INSERT INTO activity_logs (device_id, event_type, event_data)
            VALUES (%s, %s, %s)
        """, (
            device_id,
            "device_updated",
            json.dumps({
                "updated_by": current_user["username"],
                "changes": {k: v for k, v in device_update.dict(exclude_unset=True).items()}
            })
        ))
        
        connection.commit()
        
        return {"message": "Device updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating device: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail="Failed to update device")
    finally:
        cursor.close()
def verify_user_device(user_id: int, device_id: int, cursor) -> bool:###new
    """
    تتحقق إذا كان هذا الجهاز موجود ومربوط بهذا المستخدم
    """
    # تحقق من وجود الجهاز
    cursor.execute("SELECT id FROM devices WHERE id = %s", (device_id,))
    device = cursor.fetchone()
    if not device:
        return False

    # تحقق من وجود الربط مع المستخدم
    cursor.execute(
        "SELECT id FROM user_devices WHERE user_id = %s AND device_id = %s",
        (user_id, device_id)
    )
    link = cursor.fetchone()
    return bool(link)

@app.delete("/api/devices/{device_id}", response_model=dict)
async def delete_device(device_id: str, current_user: dict = Depends(get_current_user)):
    """حذف جهاز"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        # التحقق من وجود الجهاز
        existing_device = get_device_by_id(device_id)
        if not existing_device:
            raise HTTPException(status_code=404, detail="Device not found")
        cursor.execute("""
            SELECT id FROM devices
            WHERE device_id = %s AND u_id = %s
        """, (device_id, current_user["user_id"]))

        if not cursor.fetchone():
            raise HTTPException(status_code=403, detail="Not your device")
        # تسجيل النشاط
        cursor.execute("""
            INSERT INTO activity_logs (device_id, event_type, event_data)
            VALUES (%s, %s, %s)
        """, (
            device_id,
            "device_deleted",
            json.dumps({
                "deleted_by": current_user["username"]
            })
        ))
        connection.commit()
        cursor.execute("DELETE FROM devices WHERE device_id = %s", (device_id,))
        
        connection.commit()
    
        return {"message": "Device deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting device: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete device")
    finally:
        cursor.close()

@app.get("/api/rooms", response_model=List[Dict])
async def get_rooms(
    device_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        query = """
            SELECT 
                r.room_id as id,  
                r.room_id,
                r.device_id,
                r.name,
                r.led_index,
                r.led_index as ledIndex,  
                CAST(r.initial_current_limit AS FLOAT) as initial_current_limit,
                r.icon_code,
                r.created_at,
                r.updated_at
            FROM rooms r
            JOIN user_devices ud ON r.device_id = ud.device_id
            WHERE ud.user_id = %s 

        """
        params = [current_user["user_id"]]
        
        if device_id:
            query += " AND r.device_id = %s"
            params.append(device_id)
        
        query += " ORDER BY r.led_index"
        
        cursor.execute(query, tuple(params))
        rooms = cursor.fetchall()
        print(f"rooms:{rooms}")
        return rooms
    except Exception as e:
        logger.error(f"Error getting rooms: {e}")
        raise HTTPException(status_code=500, detail="Failed to get rooms")
    finally:
        cursor.close()
# === إدارة الغرف ===
@app.post("/api/rooms", response_model=dict)
async def create_room(
    room: RoomCreate,
    current_user: dict = Depends(get_current_user)
):
    """إنشاء غرفة جديدة مع جميع الإعدادات الافتراضية"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        
        # 1️⃣ تحقق أن الجهاز موجود وملك المستخدم
        cursor.execute("""
            SELECT device_id 
            FROM devices 
            WHERE device_id = %s AND u_id = %s
        """, (room.device_id, current_user["user_id"]))

        if not cursor.fetchone():
            raise HTTPException(status_code=403, detail="Not your device")

        # 2️⃣ منع تكرار led_index لنفس الجهاز
        cursor.execute("""
            SELECT 1 FROM rooms
            WHERE device_id = %s AND led_index = %s
        """, (room.device_id, room.led_index))

        if cursor.fetchone():
            raise HTTPException(
                status_code=400,
                detail="LED index already used for this device"
            )

        # 3️⃣ توليد room_id من السيرفر
        room_id = f"room_{uuid4().hex[:10]}"

        # 4️⃣ إدخال الغرفة
        cursor.execute("""
            INSERT INTO rooms (
                room_id,
                device_id,
                name,
                led_index,
                initial_current_limit,
                icon_code
            )
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            room_id,
            room.device_id,
            room.name,
            room.led_index,
            room.initial_current_limit,
            room.icon_code
        ))

        connection.commit()
        
        # 5️⃣ ⭐⭐ إصلاح: استخدام INSERT مع ON CONFLICT لـ led_states
        cursor.execute("""
            INSERT INTO led_states (
                led_index,
                device_id,
                is_on,
                desired_state,
                current_value,
                current_limit,
                created_at,
                updated_at
            )
            VALUES (%s, %s, false, false, 0.0, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (device_id, led_index) DO NOTHING
        """, (
            room.led_index,
            room.device_id,
            room.initial_current_limit
        ))

        # 6️⃣ ⭐⭐ إصلاح: استخدام INSERT مع ON CONFLICT لجدولة افتراضية
        cursor.execute("""
            INSERT INTO schedule_settings (
                led_index,
                device_id,
                start_time,
                end_time,
                days,
                created_at
            )
            VALUES (%s, %s, '00:00', '00:00', '1111111', CURRENT_TIMESTAMP)
            ON CONFLICT (device_id, led_index) DO NOTHING
        """, (
            room.led_index,
            room.device_id
        ))

        # 7️⃣ ⭐⭐ إصلاح: استخدام INSERT مع ON CONFLICT لمؤقت افتراضي
        cursor.execute("""
            INSERT INTO timer_settings (
                led_index,
                device_id,
                enabled,
                is_on_phase,
                total_on_seconds,
                total_off_seconds,
                start_time,
                created_at,
                updated_at
            )
            VALUES (%s, %s, false, true, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (device_id, led_index) DO NOTHING
        """, (
            room.led_index,
            room.device_id
        ))
        # 8️⃣ تسجيل النشاط
        cursor.execute("""
            INSERT INTO activity_logs (device_id, event_type, event_data)
            VALUES (%s, %s, %s)
        """, (
            room.device_id,
            "room_created_with_defaults",
            json.dumps({
                "room_id": room_id,
                "name": room.name,
                "led_index": room.led_index,
                "initial_current_limit": room.initial_current_limit,
                "default_settings_created": True,
                "created_by": current_user["username"]
            })
        ))
        
        connection.commit()

        # 9️⃣ ⭐⭐ التحقق من إنشاء الإعدادات
        cursor.execute("""
            SELECT 
                (SELECT COUNT(*) FROM led_states WHERE device_id = %s AND led_index = %s) as led_exists,
                (SELECT COUNT(*) FROM schedule_settings WHERE device_id = %s AND led_index = %s) as schedule_exists,
                (SELECT COUNT(*) FROM timer_settings WHERE device_id = %s AND led_index = %s) as timer_exists
        """, (room.device_id, room.led_index, room.device_id, room.led_index, room.device_id, room.led_index))
        
        checks = cursor.fetchone()
        
        return {
            "message": "Room created successfully",
            "room_id": room_id,
            "device_id": room.device_id,
            "led_index": room.led_index,
            "settings_created": {
                "led_states": checks[0] > 0,
                "schedule_settings": checks[1] > 0,
                "timer_settings": checks[2] > 0
            },
            "default_settings": {
                "current_limit": room.initial_current_limit,
                "schedule": "00:00-00:00:1111111",
                "timer": "0:0",
                "timer_enabled": False
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ خطأ في إنشاء الغرفة: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create room: {str(e)}")
    finally:
        cursor.close()
        
@app.put("/api/rooms/{room_id}", response_model=dict)
async def update_room(
    room_id: str, 
    room_update: RoomUpdate,
    current_user: dict = Depends(get_current_user)
):
    """تحديث بيانات غرفة"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        # التحقق من وجود الغرفة
        existing_room = get_room_by_id(room_id)
        if not existing_room:
            raise HTTPException(status_code=404, detail="Room not found")
        
        # بناء استعلام التحديث الديناميكي
        update_fields = []
        update_values = []
        
        if room_update.name is not None:
            update_fields.append("name = %s")
            update_values.append(room_update.name)
        if room_update.initial_current_limit is not None:
            update_fields.append("initial_current_limit = %s")
            update_values.append(room_update.initial_current_limit)
        if room_update.icon_code is not None:
            update_fields.append("icon_code = %s")
            update_values.append(room_update.icon_code)
        
        if not update_fields:
            return {"message": "No fields to update"}
        
        update_values.append(room_id)
        
        query = f"""
            UPDATE rooms 
            SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP
            WHERE room_id = %s
            RETURNING id
        """
        
        cursor.execute(query, tuple(update_values))
        
        connection.commit()
        
        # تسجيل النشاط
        cursor.execute("""
            INSERT INTO activity_logs (device_id, event_type, event_data)
            VALUES (%s, %s, %s)
        """, (
            existing_room["device_id"],
            "room_updated",
            json.dumps({
                "room_id": room_id,
                "updated_by": current_user["username"],
                "changes": {k: v for k, v in room_update.dict(exclude_unset=True).items()}
            })
        ))
        
        connection.commit()
        
        return {"message": "Room updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating room: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail="Failed to update room")
    finally:
        cursor.close()
@app.delete("/api/rooms/{room_id}", response_model=dict)
async def delete_room(room_id: str, current_user: dict = Depends(get_current_user)):
    """حذف غرفة فقط دون حذف بيانات الـ LED"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        # 1. الحصول على معلومات الغرفة قبل الحذف
        cursor.execute("""
            SELECT device_id, led_index FROM rooms 
            WHERE room_id = %s
        """, (room_id,))
        
        room_info = cursor.fetchone()
        if not room_info:
            raise HTTPException(status_code=404, detail="Room not found")
        
        device_id = room_info[0]
        led_index = room_info[1]
        
        # 2. التحقق من أن الجهاز يخص المستخدم
        cursor.execute("""
            SELECT 1 FROM devices 
            WHERE device_id = %s AND u_id = %s
        """, (device_id, current_user["user_id"]))
        
        if not cursor.fetchone():
            raise HTTPException(status_code=403, detail="Not your device")
        
        # 3. ⭐⭐ إصلاح: لا تحذف سجلات الـ LED - فقط الغرفة والإعدادات المرتبطة
        logger.info(f"🗑️ حذف الغرفة {room_id} فقط (مع الاحتفاظ بحالة LED)")
        
        # حذف من جدول timer_settings
        cursor.execute("""
            DELETE FROM timer_settings 
            WHERE device_id = %s AND led_index = %s
        """, (device_id, led_index))
        logger.info(f"   - تم حذف {cursor.rowcount} سجل من timer_settings")
        
        # حذف من جدول schedule_settings
        cursor.execute("""
            DELETE FROM schedule_settings 
            WHERE device_id = %s AND led_index = %s
        """, (device_id, led_index))
        logger.info(f"   - تم حذف {cursor.rowcount} سجل من schedule_settings")
        
        cursor.execute("""
            DELETE FROM led_states 
            WHERE device_id = %s AND led_index = %s
        """, (device_id, led_index))
        logger.info(f"   - تم حذف {cursor.rowcount} سجل من led_states")       
        # 4. حذف الغرفة نفسها
        cursor.execute("""
            DELETE FROM rooms WHERE room_id = %s
        """, (room_id,))
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Room not found")
        
        # 5. تسجيل النشاط
        cursor.execute("""
            INSERT INTO activity_logs (device_id, event_type, event_data)
            VALUES (%s, %s, %s)
        """, (
            device_id,
            "room_deleted_without_led_state",
            json.dumps({
                "room_id": room_id,
                "device_id": device_id,
                "led_index": led_index,
                "deleted_by": current_user["username"],
                "tables_cleaned": ["timer_settings", "schedule_settings"],
                "led_state_kept": True
            })
        ))
        
        connection.commit()
        
        logger.info(f"✅ تم حذف الغرفة {room_id} (تم الاحتفاظ بحالة LED)")
        return {
            "message": "Room deleted successfully (LED state preserved)",
            "deleted_room_id": room_id,
            "device_id": device_id,
            "led_index": led_index,
            "led_state_preserved": True
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ خطأ في حذف الغرفة: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete room")
    finally:
        cursor.close()
@app.get("/api/devices/{device_id}/rooms-complete", response_model=List[CompleteRoomData])
async def get_device_rooms_complete(
    device_id: str,
    current_user: dict = Depends(get_current_user)
):
    """جلب جميع بيانات الغرف لجهاز معين في دالة واحدة"""
    
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    
    try:
        print(f"🔍 جلب بيانات الغرف الكاملة للجهاز: {device_id}")
        
        # 1. التحقق من أن الجهاز يخص المستخدم
        cursor.execute("""
            SELECT 1 FROM devices 
            WHERE device_id = %s AND u_id = %s
        """, (device_id, current_user["user_id"]))
        
        if not cursor.fetchone():
            raise HTTPException(status_code=403, detail="Not your device")
        
        # 2. جلب الغرف الأساسية
        cursor.execute("""
            SELECT 
                r.room_id,
                r.device_id,
                r.name,
                r.led_index,
                CAST(r.initial_current_limit AS FLOAT) as initial_current_limit,
                r.icon_code,
                r.created_at,
                r.updated_at
            FROM rooms r
            WHERE r.device_id = %s
            ORDER BY r.led_index
        """, (device_id,))
        
        rooms = cursor.fetchall()
        print(f"📊 عدد الغرف: {len(rooms)}")
        
        if not rooms:
            return []
        
        # 3. جلب بيانات LED (desired_state وغيرها)
        cursor.execute("""
            SELECT 
                led_index,
                is_on,
                desired_state,
                CAST(current_value AS FLOAT) as current_value,
                CAST(current_limit AS FLOAT) as current_limit
            FROM led_states 
            WHERE device_id = %s
        """, (device_id,))
        
        led_states = {state['led_index']: state for state in cursor.fetchall()}
        print(f"💡 حالات LED: {len(led_states)}")
        
        # 4. جلب بيانات المؤقتات
        cursor.execute("""
            SELECT 
                led_index,
                enabled as timer_enabled,
                total_on_seconds,
                total_off_seconds,
                start_time as timer_start_time
            FROM timer_settings 
            WHERE device_id = %s
        """, (device_id,))
        
        timers = {timer['led_index']: timer for timer in cursor.fetchall()}
        print(f"⏰ المؤقتات: {len(timers)}")
        
        # 5. جلب بيانات الجدولة
        cursor.execute("""
            SELECT 
                led_index,
                start_time as schedule_start_time,
                end_time as schedule_end_time,
                days as schedule_days
            FROM schedule_settings 
            WHERE device_id = %s
        """, (device_id,))
        
        schedules = {schedule['led_index']: schedule for schedule in cursor.fetchall()}
        print(f"📅 الجداول: {len(schedules)}")
        
        # 6. دمج جميع البيانات
        complete_rooms = []
        
        for room in rooms:
            led_index = room['led_index']
            
            # بيانات LED
            led_data = led_states.get(led_index, {})
            
            # بيانات المؤقت
            timer_data = timers.get(led_index, {})
            
            # بيانات الجدولة
            schedule_data = schedules.get(led_index, {})
            
            # بناء الغرفة الكاملة
            complete_room = CompleteRoomData(
                room_id=room['room_id'],
                device_id=room['device_id'],
                name=room['name'],
                led_index=led_index,
                initial_current_limit=room['initial_current_limit'],
                icon_code=room['icon_code'],
                created_at=room['created_at'].isoformat() if isinstance(room['created_at'], datetime) else room['created_at'],
                updated_at=room['updated_at'].isoformat() if isinstance(room['updated_at'], datetime) else room['updated_at'],
                
                # بيانات LED
                desired_state=led_data.get('desired_state', False),
                is_on=led_data.get('is_on', False),
                current_value=led_data.get('current_value', 0.0),
                current_limit=led_data.get('current_limit', room['initial_current_limit']),
                
                # بيانات المؤقت
                timer_enabled=timer_data.get('timer_enabled', False),
                total_on_seconds=timer_data.get('total_on_seconds', 0),
                total_off_seconds=timer_data.get('total_off_seconds', 0),
                timer_start_time=timer_data.get('timer_start_time').isoformat() 
                    if timer_data.get('timer_start_time') and isinstance(timer_data.get('timer_start_time'), datetime) 
                    else timer_data.get('timer_start_time'),
                
                # بيانات الجدولة
                schedule_enabled=bool(schedule_data),
                schedule_start_time=schedule_data.get('schedule_start_time'),
                schedule_end_time=schedule_data.get('schedule_end_time'),
                schedule_days=schedule_data.get('schedule_days', '1111111')
            )
            
            complete_rooms.append(complete_room)
        
        print(f"✅ تم جلب {len(complete_rooms)} غرفة كاملة")
        return complete_rooms
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ خطأ في جلب بيانات الغرف الكاملة: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get complete room data: {str(e)}")
    finally:
        cursor.close()
@app.put("/api/current-limit/{led_index}", response_model=dict)
async def update_current_limit(
    led_index: int,
    payload: dict = Body(...),
    current_user: dict = Depends(get_current_user)
):
    device_id = payload.get("device_id")
    current_limit = payload.get("current_limit")

    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")
    """تحديث الحد الأعلى للتيار"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        # البحث عن السجل الحالي
        cursor.execute("""
            SELECT id FROM led_states 
            WHERE led_index = %s AND device_id = %s
        """, (led_index, device_id))
        
        existing = cursor.fetchone()
                
        if not existing:
            raise HTTPException(
                status_code=404,
                detail="LED state not found. Room must exist first."
            )

        cursor.execute("""
            UPDATE led_states
            SET current_limit = %s, updated_at = CURRENT_TIMESTAMP
            WHERE led_index = %s AND device_id = %s
        """, (current_limit, led_index, device_id))

        connection.commit()
        
        # تسجيل النشاط
        cursor.execute("""
            INSERT INTO activity_logs (device_id, event_type, event_data)
            VALUES (%s, %s, %s)
        """, (
            device_id,
            "current_limit_update",
            json.dumps({
                "led_index": led_index,
                "current_limit": current_limit,
                "updated_by": current_user["username"]
            })
        ))
        
        connection.commit()
        
        return {"message": f"Current limit for LED {led_index} updated successfully"}
    except Exception as e:
        logger.error(f"Error updating current limit: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail="Failed to update current limit")
    finally:
        cursor.close()
# === إدارة إعدادات المؤقت ===
# === إدارة المؤقت ===
@app.post("/api/devices/{device_id}/timer")
async def create_or_update_device_timer(
    device_id: str,
    timer_request: TimerSettings,
    current_user: dict = Depends(get_current_user)
):
    """إنشاء أو تحديث مؤقت للجهاز (يُحفظ في قاعدة البيانات فقط - لا إرسال)"""
    
    device = get_device_by_id(device_id)
    if not device or device["u_id"] != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="Not your device")
    
    logger.info(f"⏰ حفظ مؤقت للجهاز {device_id}: LED {timer_request.led_index}")
    
    connection = db.get_connection()
    cursor = connection.cursor()
    
    try:
        # التحقق من وجود سجل مسبق
        cursor.execute("""
            SELECT id FROM timer_settings 
            WHERE led_index = %s AND device_id = %s
        """, (timer_request.led_index, device_id))
        
        existing = cursor.fetchone()
        
        if existing:
            # تحديث المؤقت الموجود
            cursor.execute("""
                UPDATE timer_settings 
                SET total_on_seconds = %s, 
                    total_off_seconds = %s, 
                    enabled = %s,
                    is_on_phase = true,
                    start_time = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (
                timer_request.on_duration_seconds,
                timer_request.off_duration_seconds,
                timer_request.enabled,
                existing[0]
            ))
        else:
            # إنشاء مؤقت جديد
            cursor.execute("""
                INSERT INTO timer_settings 
                (led_index, device_id, total_on_seconds, total_off_seconds, 
                 enabled, is_on_phase, start_time)
                VALUES (%s, %s, %s, %s, %s, true, CURRENT_TIMESTAMP)
            """, (
                timer_request.led_index,
                device_id,
                timer_request.on_duration_seconds,
                timer_request.off_duration_seconds,
                timer_request.enabled
            ))
        
        connection.commit()
        logger.info(f"💾 تم حفظ المؤقت في قاعدة البيانات: LED {timer_request.led_index}")
        
        # ⭐⭐ لا نرسل أي شيء لـ ESP هنا! سيتم الإرسال عند التشغيل
        
        return {
            "status": "saved",
            "message": "تم حفظ إعدادات المؤقت في قاعدة البيانات",
            "device_id": device_id,
            "led_index": timer_request.led_index
        }
            
    except Exception as e:
        logger.error(f"❌ خطأ في حفظ المؤقت: {e}", exc_info=True)
        connection.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save timer: {str(e)}")
    finally:
        cursor.close()




    # 6. تسجيل النشاط
    connection = db.get_connection()
    cursor = connection.cursor()
    cursor.execute("""
        UPDATE timer_settings
        SET start_time = CURRENT_TIMESTAMP
        WHERE device_id = %s AND led_index = %s AND enabled = true
    """, (device_id, cmd.led_index))    
    connection.commit()
    cursor.execute("""
        INSERT INTO activity_logs (device_id, event_type, event_data)
        VALUES (%s, %s, %s)
    """, (
        device_id,
        "command_with_timer",
        json.dumps({
            "led_index": cmd.led_index,
            "state": cmd.state,
            "command_id": command_id,
            "mqtt_message": message,
            "had_timer": timer is not None and timer['enabled'],
            "by_user": current_user["username"]
        })
    ))
    connection.commit()
    cursor.close()
    
    return {
        "status": "sent",
        "command_id": command_id,
        "has_timer": timer is not None and timer['enabled'],
        "message": message
    }

@app.get("/api/devices/{device_id}/timer/{led_index}/current-state")
async def get_device_timer_current_state(
    device_id: str,
    led_index: int,
    current_user: dict = Depends(get_current_user)
):
    """الحصول على الحالة الحالية لمؤقت جهاز معين"""
    
    # 1. التحقق من صلاحية الجهاز
    device = get_device_by_id(device_id)
    if not device or device["u_id"] != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="Not your device")
    
    # 2. جلب المؤقت من قاعدة البيانات
    timer = get_timer_setting(led_index, device_id)
    if not timer or not timer['enabled']:
        return {"active": False, "message": "No active timer found"}
    
    # 3. حساب التقدم الحالي
    progress = calculate_timer_progress(timer)
    if progress is None:
        return {"active": False}
    
    return {
        "active": True,
        "device_id": device_id,
        "led_index": led_index,
        "current_state": progress,
        "settings": {
            "total_on_seconds": timer['total_on_seconds'],
            "total_off_seconds": timer['total_off_seconds'],
            "start_time": timer['start_time'].isoformat() if timer['start_time'] else None,
            "is_on_phase": timer['is_on_phase']
        }
    }

@app.get("/api/devices/{device_id}/timers/status")
async def get_device_timers_status(
    device_id: str,
    current_user: dict = Depends(get_current_user)
):
    """الحصول على حالة جميع المؤقتات لجهاز معين - الإصدار المحسن"""
    
    device = get_device_by_id(device_id)
    if not device or device["u_id"] != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="Not your device")
    
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    
    try:
        print(f"🔍 البحث عن مؤقتات للجهاز: {device_id}")
        
        # ⭐⭐ 1. جلب جميع المؤقتات للجهاز
        cursor.execute("""
            SELECT ts.*, ls.desired_state 
            FROM timer_settings ts
            LEFT JOIN led_states ls ON ts.device_id = ls.device_id AND ts.led_index = ls.led_index
            WHERE ts.device_id = %s 
            ORDER BY ts.led_index
        """, (device_id,))
        timers = cursor.fetchall()
        
        print(f"📊 عدد المؤقتات الموجودة في DB: {len(timers)}")
        
        result = []
        for timer in timers:
            led_index = timer['led_index']
            total_on = timer.get('total_on_seconds', 0)
            total_off = timer.get('total_off_seconds', 0)
            desired_state = timer.get('desired_state', False)
            db_enabled = timer.get('enabled', False)
            
            print(f"   - مؤقت LED {led_index}: total_on={total_on}, total_off={total_off}, desired_state={desired_state}, db_enabled={db_enabled}")
            
            # ⭐⭐ 2. حساب enabled تلقائياً
            # الشرط: desired_state = True AND total_on > 0 AND total_off > 0
            auto_enabled = desired_state and total_on > 0 and total_off > 0
            
            # ⭐⭐ 3. إذا كانت القيم مختلفة، تحديث قاعدة البيانات
            if db_enabled != auto_enabled:
                print(f"   🔄 تحديث enabled من {db_enabled} إلى {auto_enabled}")
                update_cursor = connection.cursor()
                update_cursor.execute("""
                    UPDATE timer_settings 
                    SET enabled = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE device_id = %s AND led_index = %s
                """, (auto_enabled, device_id, led_index))
                connection.commit()
                update_cursor.close()
                timer['enabled'] = auto_enabled
            
            # ⭐⭐ 4. إذا كان المؤقت مفعلاً، حساب التقدم
            if auto_enabled:
                print(f"   ✅ مؤقت LED {led_index} مفعل تلقائياً")
                progress = calculate_timer_progress(timer)
                if progress:
                    result.append({
                        "led_index": led_index,
                        "enabled": auto_enabled,
                        "total_on_seconds": total_on,  # ⭐ إضافة في المستوى الرئيسي
                        "total_off_seconds": total_off,  # ⭐ إضافة في المستوى الرئيسي
                        "desired_state": desired_state,  # ⭐ إضافة
                        "current_state": progress if progress else None,
                        "settings": {
                            "total_on_seconds": total_on,
                            "total_off_seconds": total_off,
                            "start_time": timer['start_time'].isoformat() if timer['start_time'] else None,
                            "is_on_phase": timer['is_on_phase']
                        }
                    })
                    print(f"   📤 مضاف للنتيجة مع enabled={auto_enabled}")
            else:
                print(f"   ❌ مؤقت LED {led_index} غير مفعل: desired_state={desired_state}")
        
        print(f"📤 عدد المؤقتات المرسلة: {len(result)}")
        return result
    except Exception as e:
        logger.error(f"Error getting device timers status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get timers status")
    finally:
        cursor.close()

# === إدارة إعدادات الجدولة ===
def create_timer_setting(led_index: int, device_id: str, on_seconds: int, off_seconds: int, enabled: bool = True):
    """إنشاء أو تحديث إعداد مؤقت"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        # 1. التحقق من وجود سجل
        cursor.execute("""
            SELECT id FROM timer_settings 
            WHERE led_index = %s AND device_id = %s
        """, (led_index, device_id))
        
        existing = cursor.fetchone()
        
        if existing:
            # 2. إذا موجود، قم بالتحديث
            cursor.execute("""
                UPDATE timer_settings 
                SET total_on_seconds = %s, total_off_seconds = %s, enabled = %s, 
                    is_on_phase = true, start_time = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (on_seconds, off_seconds, enabled, existing[0]))
        else:
            # 3. إذا غير موجود، قم بالإدخال
            cursor.execute("""
                INSERT INTO timer_settings 
                (led_index, device_id, total_on_seconds, total_off_seconds, enabled, is_on_phase, start_time)
                VALUES (%s, %s, %s, %s, %s, true, CURRENT_TIMESTAMP)
            """, (led_index, device_id, on_seconds, off_seconds, enabled))
        
        connection.commit()
        return True
    except Exception as e:
        logger.error(f"Error creating/updating timer setting: {e}")
        connection.rollback()
        return False
    finally:
        cursor.close()

@app.get("/api/schedule_settings", response_model=List[Dict])
async def get_schedule_settings(
    device_id: Optional[str] = "default",
    current_user: dict = Depends(get_current_user)
):
    """الحصول على إعدادات الجدولة"""
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            "SELECT * FROM schedule_settings WHERE device_id = %s ORDER BY led_index",
            (device_id,)
        )
        schedule_settings = cursor.fetchall()
        return schedule_settings
    except Exception as e:
        logger.error(f"Error getting schedule settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to get schedule settings")
    finally:
        cursor.close()
@app.post("/api/schedule_settings", response_model=dict)
async def create_schedule_setting(
    schedule_setting: ScheduleSettingCreate,
    current_user: dict = Depends(get_current_user)
):
    """إنشاء أو تحديث إعداد جدولة"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        # 1. تحقق أولاً من وجود سجل
        cursor.execute("""
            SELECT id FROM schedule_settings 
            WHERE led_index = %s AND device_id = %s
        """, (schedule_setting.led_index, schedule_setting.device_id))
        
        existing = cursor.fetchone()
        
        if existing:
            # ⭐⭐ التصحيح: استخدم الفهرس العددي [0] لأن existing هو tuple
            existing_id = existing[0]  # <-- هذا هو التصحيح!
            print(f"📌 تم العثور على سجل موجود بالـ ID: {existing_id}")
            
            # 2. إذا موجود، قم بالتحديث
            cursor.execute("""
                UPDATE schedule_settings 
                SET start_time = %s, end_time = %s, days = %s, created_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (
                schedule_setting.start_time,
                schedule_setting.end_time,
                schedule_setting.days,
                existing_id  # <-- استخدام المتغير الجديد
            ))
        else:
            # 3. إذا غير موجود، قم بالإدخال
            cursor.execute("""
                INSERT INTO schedule_settings 
                (led_index, start_time, end_time, days, device_id)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                schedule_setting.led_index,
                schedule_setting.start_time,
                schedule_setting.end_time,
                schedule_setting.days,
                schedule_setting.device_id
            ))
        
        connection.commit()
        
        # تسجيل النشاط
        cursor.execute("""
            INSERT INTO activity_logs (device_id, event_type, event_data)
            VALUES (%s, %s, %s)
        """, (
            schedule_setting.device_id,
            "schedule_setting_updated",
            json.dumps({
                "led_index": schedule_setting.led_index,
                "start_time": schedule_setting.start_time,
                "end_time": schedule_setting.end_time,
                "updated_by": current_user["username"]
            })
        ))
        
        connection.commit()
        
        print(f"✅ تم حفظ الجدولة للغرفة {schedule_setting.led_index}")
        return {"message": "Schedule setting saved successfully"}
    except Exception as e:
        print(f"❌ خطأ في حفظ الجدولة: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail="Failed to save schedule setting")
    finally:
        cursor.close()
@app.put("/api/schedule_settings/{led_index}", response_model=dict)
async def update_schedule_setting(
    led_index: int,
    schedule_update: ScheduleSettingUpdate,
    device_id: str = "default",
    current_user: dict = Depends(get_current_user)
):
    """تحديث إعداد جدولة"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        # التحقق من وجود الإعداد
        existing_schedule = get_schedule_setting(led_index, device_id)
        if not existing_schedule:
            raise HTTPException(status_code=404, detail="Schedule setting not found")
        
        # بناء استعلام التحديث الديناميكي
        update_fields = []
        update_values = []
        
        if schedule_update.start_time is not None:
            update_fields.append("start_time = %s")
            update_values.append(schedule_update.start_time)
        if schedule_update.end_time is not None:
            update_fields.append("end_time = %s")
            update_values.append(schedule_update.end_time)
        if schedule_update.days is not None:
            update_fields.append("days = %s")
            update_values.append(schedule_update.days)
        
        if not update_fields:
            return {"message": "No fields to update"}
        
        update_values.extend([led_index, device_id])
        
        query = f"""
            UPDATE schedule_settings 
            SET {', '.join(update_fields)}, created_at = CURRENT_TIMESTAMP
            WHERE led_index = %s AND device_id = %s
            RETURNING id
        """
        
        cursor.execute(query, tuple(update_values))
        
        connection.commit()
        
        # تسجيل النشاط
        cursor.execute("""
            INSERT INTO activity_logs (device_id, event_type, event_data)
            VALUES (%s, %s, %s)
        """, (
            device_id,
            "schedule_setting_updated",
            json.dumps({
                "led_index": led_index,
                "updated_by": current_user["username"],
                "changes": {k: v for k, v in schedule_update.dict(exclude_unset=True).items()}
            })
        ))
        
        connection.commit()
        
        return {"message": "Schedule setting updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating schedule setting: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail="Failed to update schedule setting")
    finally:
        cursor.close()

# === نقاط نهاية متقدمة ===
@app.post("/api/activity_logs", response_model=dict)
async def create_activity_log(
    activity_log: ActivityLogCreate,
    current_user: dict = Depends(get_current_user)
):
    """إنشاء سجل نشاط"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        event_data_str = json.dumps(activity_log.event_data) if activity_log.event_data else None
        
        cursor.execute("""
            INSERT INTO activity_logs (device_id, event_type, event_data)
            VALUES (%s, %s, %s)
            RETURNING id
        """, (
            activity_log.device_id,
            activity_log.event_type,
            event_data_str
        ))
        
        connection.commit()
        return {"message": "Activity log created successfully"}
    except Exception as e:
        logger.error(f"Error creating activity log: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail="Failed to create activity log")
    finally:
        cursor.close()

@app.post("/api/statistics/advanced", response_model=Dict)
async def get_advanced_statistics(
    stats_request: StatisticsRequest,
    current_user: dict = Depends(get_current_user)
):
    """الحصول على إحصائيات متقدمة"""
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        stats = {}
        
        # بناء شروط WHERE
        conditions = []
        params = []
        
        if stats_request.device_id:
            conditions.append("device_id = %s")
            params.append(stats_request.device_id)
        
        if stats_request.start_date:
            conditions.append("created_at >= %s")
            params.append(stats_request.start_date)
        
        if stats_request.end_date:
            conditions.append("created_at <= %s")
            params.append(stats_request.end_date)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        # 1. إحصائيات المصابيح
        cursor.execute(f"""
            SELECT 
                COUNT(*) as total_leds,
                SUM(CASE WHEN is_on THEN 1 ELSE 0 END) as on_count,
                SUM(CASE WHEN NOT is_on THEN 1 ELSE 0 END) as off_count,
                AVG(current_value) as avg_current,
                MAX(current_value) as max_current
            FROM led_states 
            WHERE {where_clause}
        """, tuple(params))
        stats["led_statistics"] = cursor.fetchone()
        
        # 2. إحصائيات المؤقتات
        cursor.execute(f"""
            SELECT 
                COUNT(*) as total_timers,
                SUM(CASE WHEN enabled THEN 1 ELSE 0 END) as enabled_count,
                SUM(CASE WHEN NOT enabled THEN 1 ELSE 0 END) as disabled_count
            FROM timer_settings 
            WHERE {where_clause}
        """, tuple(params))
        stats["timer_statistics"] = cursor.fetchone()
        
        # 3. إحصائيات الجدولة
        cursor.execute(f"""
            SELECT COUNT(*) as total_schedules FROM schedule_settings 
            WHERE {where_clause}
        """, tuple(params))
        stats["schedule_statistics"] = cursor.fetchone()
        
        # 4. نشاط النظام (آخر 24 ساعة)
        cursor.execute("""
            SELECT 
                COUNT(*) as total_activities,
                COUNT(DISTINCT event_type) as unique_event_types
            FROM activity_logs 
            WHERE created_at >= NOW() - INTERVAL '24 hours'
        """)
        stats["activity_24h"] = cursor.fetchone()
        
        # 5. إحصائيات MQTT
        cursor.execute("""
            SELECT 
                COUNT(*) as total_messages,
                COUNT(DISTINCT topic) as unique_topics,
                MAX(arrived) as last_message_time
            FROM mqtt_messages 
            WHERE arrived >= NOW() - INTERVAL '24 hours'
        """)
        stats["mqtt_statistics"] = cursor.fetchone()
        
        return stats
    except Exception as e:
        logger.error(f"Error getting advanced statistics: {e}")
        raise HTTPException(status_code=500, detail="Failed to get statistics")
    finally:
        cursor.close()

@app.get("/api/system/health", response_model=Dict)
async def system_health():
    """فحص صحة النظام"""
    try:
        # فحص اتصال قاعدة البيانات
        connection = db.get_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        db_status = "healthy"
        cursor.close()
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"
    
    return {
        "status": "running",
        "database": db_status,
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }

# === نقاط نهاية للتنظيف والصيانة ===
@app.post("/api/system/cleanup", response_model=dict)
async def cleanup_system(
    days_to_keep: int = 30,
    current_user: dict = Depends(get_current_user)
):
    """تنظيف البيانات القديمة"""
    connection = db.get_connection()
    cursor = connection.cursor()
    try:
        # تنظيف سجلات النشاط القديمة
        cursor.execute("""
            DELETE FROM activity_logs 
            WHERE created_at < NOW() - INTERVAL '%s days'
        """, (days_to_keep,))
        activity_deleted = cursor.rowcount
        
        # تنظيف رسائل MQTT القديمة
        cursor.execute("""
            DELETE FROM mqtt_messages 
            WHERE arrived < NOW() - INTERVAL '%s days'
        """, (days_to_keep,))
        mqtt_deleted = cursor.rowcount
        
        connection.commit()
        
        return {
            "message": "Cleanup completed",
            "activity_logs_deleted": activity_deleted,
            "mqtt_messages_deleted": mqtt_deleted,
            "total_deleted": activity_deleted + mqtt_deleted
        }
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail="Failed to cleanup system")
    finally:
        cursor.close()
# === البحث ===
@app.get("/api/search", response_model=Dict)
async def search(
    query: str,
    search_type: Optional[str] = "all",
    device_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """بحث في البيانات"""
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        results = {}
        search_query = f"%{query}%"
        
        if search_type in ["all", "rooms"]:
            if device_id:
                cursor.execute("""
                    SELECT * FROM rooms 
                    WHERE (name ILIKE %s OR room_id ILIKE %s) 
                    AND device_id = %s
                """, (search_query, search_query, device_id))
            else:
                cursor.execute("""
                    SELECT * FROM rooms 
                    WHERE name ILIKE %s OR room_id ILIKE %s
                """, (search_query, search_query))
            results["rooms"] = cursor.fetchall()
        
        if search_type in ["all", "devices"]:
            cursor.execute("""
                SELECT * FROM devices 
                WHERE name ILIKE %s OR device_id ILIKE %s
            """, (search_query, search_query))
            results["devices"] = cursor.fetchall()
        
        if search_type in ["all", "mqtt"]:
            if device_id:
                # لا يوجد device_id في جدول mqtt_messages، لذا نبحث في payload
                cursor.execute("""
                    SELECT * FROM mqtt_messages 
                    WHERE topic ILIKE %s OR payload ILIKE %s
                    LIMIT 50
                """, (search_query, search_query))
            else:
                cursor.execute("""
                    SELECT * FROM mqtt_messages 
                    WHERE topic ILIKE %s OR payload ILIKE %s
                    LIMIT 50
                """, (search_query, search_query))
            results["mqtt_messages"] = cursor.fetchall()
        
        if search_type in ["all", "activity"]:
            if device_id:
                cursor.execute("""
                    SELECT * FROM activity_logs 
                    WHERE (event_type ILIKE %s OR event_data::text ILIKE %s)
                    AND device_id = %s
                    ORDER BY created_at DESC
                    LIMIT 50
                """, (search_query, search_query, device_id))
            else:
                cursor.execute("""
                    SELECT * FROM activity_logs 
                    WHERE event_type ILIKE %s OR event_data::text ILIKE %s
                    ORDER BY created_at DESC
                    LIMIT 50
                """, (search_query, search_query))
            results["activity_logs"] = cursor.fetchall()
        
        return {
            "query": query,
            "search_type": search_type,
            "results": results,
            "total_results": sum(len(v) for v in results.values())
        }
    except Exception as e:
        logger.error(f"Error during search: {e}")
        raise HTTPException(status_code=500, detail="Failed to search")
    finally:
        cursor.close()
@app.post("/api/system/migrate-room-defaults")
async def migrate_room_default_settings(
    current_user: dict = Depends(get_current_user)
):
    """ترحيل جميع الغرف الحالية وإضافة الإعدادات المفقودة"""
    
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    
    try:
        # 1. جلب جميع غرف المستخدم
        cursor.execute("""
            SELECT r.room_id, r.device_id, r.led_index, r.initial_current_limit
            FROM rooms r
            JOIN devices d ON r.device_id = d.device_id
            WHERE d.u_id = %s
        """, (current_user["user_id"],))
        
        rooms = cursor.fetchall()
        logger.info(f"📋 تم العثور على {len(rooms)} غرفة للترحيل")
        
        migrated_count = 0
        results = []
        
        for room in rooms:
            room_id = room['room_id']
            device_id = room['device_id']
            led_index = room['led_index']
            current_limit = room['initial_current_limit']
            
            # 2. التحقق من وجود إعدادات LED
            cursor.execute("""
                SELECT 1 FROM led_states 
                WHERE device_id = %s AND led_index = %s
            """, (device_id, led_index))
            
            if not cursor.fetchone():
                # إنشاء إعدادات LED
                cursor.execute("""
                    INSERT INTO led_states (
                        led_index, device_id, is_on, desired_state, 
                        current_value, current_limit, created_at, updated_at
                    )
                    VALUES (%s, %s, false, false, 0.0, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (led_index, device_id, current_limit))
                logger.info(f"   - تم إنشاء إعدادات LED للغرفة {room_id}")
            
            # 3. التحقق من وجود إعدادات الجدولة
            cursor.execute("""
                SELECT 1 FROM schedule_settings 
                WHERE device_id = %s AND led_index = %s
            """, (device_id, led_index))
            
            if not cursor.fetchone():
                # إنشاء إعدادات الجدولة الافتراضية
                cursor.execute("""
                    INSERT INTO schedule_settings (
                        led_index, device_id, start_time, end_time, days, created_at
                    )
                    VALUES (%s, %s, '00:00', '00:00', '1111111', CURRENT_TIMESTAMP)
                """, (led_index, device_id))
                logger.info(f"   - تم إنشاء إعدادات الجدولة الافتراضية للغرفة {room_id}")
            
            # 4. التحقق من وجود إعدادات المؤقت
            cursor.execute("""
                SELECT 1 FROM timer_settings 
                WHERE device_id = %s AND led_index = %s
            """, (device_id, led_index))
            
            if not cursor.fetchone():
                # إنشاء إعدادات المؤقت الافتراضية
                cursor.execute("""
                    INSERT INTO timer_settings (
                        led_index, device_id, enabled, is_on_phase, 
                        total_on_seconds, total_off_seconds, 
                        start_time, created_at, updated_at
                    )
                    VALUES (%s, %s, false, true, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (led_index, device_id))
                logger.info(f"   - تم إنشاء إعدادات المؤقت الافتراضية للغرفة {room_id}")
            
            migrated_count += 1
            results.append({
                "room_id": room_id,
                "device_id": device_id,
                "led_index": led_index,
                "migrated": True
            })
        
        connection.commit()
        
        return {
            "message": "Migration completed successfully",
            "total_rooms": len(rooms),
            "migrated_rooms": migrated_count,
            "results": results
        }
        
    except Exception as e:
        logger.error(f"❌ خطأ في ترحيل الإعدادات: {e}")
        connection.rollback()
        raise HTTPException(status_code=500, detail=f"Migration failed: {str(e)}")
    finally:
        cursor.close()
# === التصدير والاستيراد ===
@app.get("/api/export/{data_type}", response_model=Dict)
async def export_data(
    data_type: str,
    device_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """تصدير البيانات"""
    connection = db.get_connection()
    cursor = connection.cursor(cursor_factory=RealDictCursor)
    try:
        if data_type == "all":
            # تصدير كل البيانات
            data = {}
            
            # الأجهزة
            cursor.execute("SELECT * FROM devices")
            data["devices"] = cursor.fetchall()
            
            # الغرف
            if device_id:
                cursor.execute("SELECT * FROM rooms WHERE device_id = %s", (device_id,))
            else:
                cursor.execute("SELECT * FROM rooms")
            data["rooms"] = cursor.fetchall()
            
            # حالات المصابيح
            if device_id:
                cursor.execute("SELECT * FROM led_states WHERE device_id = %s", (device_id,))
            else:
                cursor.execute("SELECT * FROM led_states")
            data["led_states"] = cursor.fetchall()
            
            # إعدادات المؤقت
            if device_id:
                cursor.execute("SELECT * FROM timer_settings WHERE device_id = %s", (device_id,))
            else:
                cursor.execute("SELECT * FROM timer_settings")
            data["timer_settings"] = cursor.fetchall()
            
            # إعدادات الجدولة
            if device_id:
                cursor.execute("SELECT * FROM schedule_settings WHERE device_id = %s", (device_id,))
            else:
                cursor.execute("SELECT * FROM schedule_settings")
            data["schedule_settings"] = cursor.fetchall()
            
            return {
                "export_type": "all",
                "device_id": device_id,
                "exported_by": current_user["username"],
                "timestamp": datetime.now().isoformat(),
                "data": data
            }
        
        elif data_type == "configuration":
            # تصدير التكوين فقط
            data = {}
            
            # الأجهزة
            cursor.execute("SELECT * FROM devices")
            data["devices"] = cursor.fetchall()
            
            # الغرف
            if device_id:
                cursor.execute("SELECT * FROM rooms WHERE device_id = %s", (device_id,))
            else:
                cursor.execute("SELECT * FROM rooms")
            data["rooms"] = cursor.fetchall()
            
            return {
                "export_type": "configuration",
                "device_id": device_id,
                "exported_by": current_user["username"],
                "timestamp": datetime.now().isoformat(),
                "data": data
            }
        
        else:
            raise HTTPException(status_code=400, detail="Invalid export type")
        
    except Exception as e:
        logger.error(f"Error exporting data: {e}")
        raise HTTPException(status_code=500, detail="Failed to export data")
    finally:
        cursor.close()
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)