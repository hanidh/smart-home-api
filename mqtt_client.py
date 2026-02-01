import json
import ssl
import logging
import paho.mqtt.client as mqtt
from datetime import datetime
from db import db
import os
import time

logger = logging.getLogger(__name__)

# ========== إعدادات الاتصال ==========
MQTT_HOST = os.getenv("MQTT_BROKER", "k117111f.ala.us-east-1.emqxsl.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", 8883))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

mqtt_client = None
MQTT_TOPIC = "#"

# ========== تعريفات الدوال (يجب أن تكون في المستوى العالمي) ==========

def on_connect(client, userdata, flags, rc, properties=None):
    """معالج حدث الاتصال - يجب أن يكون في المستوى العالمي"""
    if rc == 0:
        logger.info("✅ MQTT Connected to EMQX Cloud")
        client.subscribe("home/+/control")
        logger.info("📡 Subscribed to home/+/control")

        # اشترك في موضوعات الـ LED
        for i in range(1, 9):
            client.subscribe(f"smarthome/led{i}/state")
    else:
        logger.error(f"❌ MQTT Connection failed with code {rc}")
        error_codes = {
            1: "Connection refused - incorrect protocol version",
            2: "Connection refused - invalid client identifier",
            3: "Connection refused - server unavailable",
            4: "Connection refused - bad username or password",
            5: "Connection refused - not authorised"
        }
        logger.error(f"🔍 Error meaning: {error_codes.get(rc, 'Unknown error')}")

def on_disconnect(client, userdata, rc, properties=None):
    """معالج حدث قطع الاتصال"""
    if rc != 0:
        logger.warning(f"⚠️ MQTT unexpected disconnect, rc={rc}")
    else:
        logger.info("ℹ️ MQTT disconnected normally")

def on_message(client, userdata, msg):
    payload_raw = msg.payload.decode().strip()
    topic = msg.topic

    logger.info(f"📥 MQTT received: {topic} - {payload_raw[:80]}")

    conn = db.get_connection()
    cur = conn.cursor()

    try:
        # =========================
        # 1️⃣ ACK من ESP (46:success)
        # =========================
        if ":" in payload_raw and payload_raw.split(":")[0].isdigit():
            command_id, status = payload_raw.split(":", 1)

            logger.info(f"✅ ACK received for command {command_id}: {status}")

            cur.execute("""
                INSERT INTO activity_logs (event_type, event_data)
                VALUES (%s, %s)
            """, (
                "device_command_ack",
                json.dumps({
                    "command_id": int(command_id),
                    "status": status,
                    "topic": topic
                })
            ))

            conn.commit()
            return

        # =========================
        # 2️⃣ مصفوفة حالات is_on
        # =========================
        if payload_raw.startswith("[") and payload_raw.endswith("]"):
            values = json.loads(payload_raw)

            # نحدد نوعها: bool أو float
            is_boolean_array = all(v in (0, 1) for v in values)

            # استخراج device_id من التوبيك
            # home/{user_id}/{device_id}/state
            parts = topic.split("/")
            device_id = parts[2] if len(parts) >= 3 else None

            if not device_id:
                logger.warning("⚠️ Device ID not found in topic")
                return

            for idx, value in enumerate(values):
                if is_boolean_array:
                    # تحديث is_on
                    cur.execute("""
                        UPDATE led_states
                        SET is_on = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE device_id = %s AND led_index = %s
                    """, (bool(value), device_id, idx))

                else:
                    # تحديث current_value
                    cur.execute("""
                        UPDATE led_states
                        SET current_value = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE device_id = %s AND led_index = %s
                    """, (float(value), device_id, idx))

            conn.commit()
            logger.info(f"🔄 LED states updated for device {device_id}")
            return

        # =========================
        # 3️⃣ تخزين عام (fallback)
        # =========================
        cur.execute("""
            INSERT INTO mqtt_messages
            (topic, payload, qos, retain, client_id, arrived)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            topic,
            payload_raw,
            msg.qos,
            msg.retain,
            client._client_id.decode() if client._client_id else None,
            datetime.now()
        ))

        conn.commit()

    except Exception as e:
        conn.rollback()
        logger.error(f"❌ MQTT processing error: {e}", exc_info=True)

    finally:
        cur.close()

# ========== الدوال الرئيسية ==========

def start_mqtt():
    """تشغيل عميل MQTT"""
    global mqtt_client
    
    try:
        logger.info("🔧 Setting up MQTT client...")
        
        import uuid
        client_id = f"smarthome-{uuid.uuid4().hex[:8]}"
        
        # استخدام الإصدار الصحيح من API
        try:
            # إصدار VERSION2 يدعم 5 معاملات في on_connect
            mqtt_client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id,
                protocol=mqtt.MQTTv5
            )
            logger.info("✅ Using MQTT API VERSION2")
        except Exception as e:
            logger.warning(f"⚠️ VERSION2 not available, using default: {e}")
            mqtt_client = mqtt.Client(client_id=client_id)
        
        logger.info(f"🔧 MQTT Client ID: {client_id}")
        logger.info(f"🔧 Connecting to: {MQTT_HOST}:{MQTT_PORT}")
        logger.info(f"🔧 Username: {MQTT_USERNAME}")
        
        if MQTT_USERNAME and MQTT_PASSWORD:
            mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
            logger.info("🔧 Using authentication")
        else:
            logger.warning("⚠️ No MQTT credentials provided")
        
        # ✅ إعداد TLS مع شهادة CA
        try:
            # أولاً: جرب مع شهادة CA
            ca_cert_path = "./emqxsl-ca.crt"
            if os.path.exists(ca_cert_path):
                mqtt_client.tls_set(
                    ca_certs=ca_cert_path,
                    tls_version=ssl.PROTOCOL_TLS
                )
                logger.info(f"✅ Using CA certificate: {ca_cert_path}")
            else:
                # إذا لم توجد الشهادة، استخدم SSL بدون تحقق (للتجربة فقط)
                logger.warning("⚠️ CA certificate not found, using insecure TLS")
                mqtt_client.tls_set(tls_version=ssl.PROTOCOL_TLS)
                mqtt_client.tls_insecure_set(True)
        except Exception as e:
            logger.error(f"❌ TLS setup error: {e}")
            # محاولة بديلة
            mqtt_client.tls_set()
            mqtt_client.tls_insecure_set(True)
        
        # 🔗 تعيين معالجات الأحداث (الدوال العالمية)
        mqtt_client.on_connect = on_connect
        mqtt_client.on_message = on_message
        mqtt_client.on_disconnect = on_disconnect
        
        # إعداد إعادة الاتصال التلقائي
        mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)
        
        # الاتصال
        logger.info("🔌 Connecting to EMQX Cloud...")
        mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        
        # بدء loop في الخلفية
        mqtt_client.loop_start()
        
        # انتظار الاتصال
        time.sleep(3)
        
        if mqtt_client.is_connected():
            logger.info("✅ MQTT connected and running in background")
            
            # إرسال رسالة اختبارية
            try:
                mqtt_client.publish("smarthome/backend/status", "online", qos=1)
                logger.info("📤 Sent status message")
            except Exception as e:
                logger.error(f"❌ Failed to send test message: {e}")
            
            return True
        else:
            logger.warning("⚠️ MQTT not connected yet (will retry in background)")
            return True  # نعود بـ True لأن loop يعمل في الخلفية
            
    except Exception as e:
        logger.error(f"❌ Failed to start MQTT: {e}")
        import traceback
        traceback.print_exc()
        return False

def stop_mqtt():
    """إيقاف عميل MQTT"""
    global mqtt_client
    if mqtt_client:
        try:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
            logger.info("🛑 MQTT disconnected")
        except Exception as e:
            logger.error(f"❌ Error stopping MQTT: {e}")
        finally:
            mqtt_client = None

def publish_message(topic, payload, qos=0, retain=False):
    """نشر رسالة MQTT"""
    global mqtt_client
    if mqtt_client and mqtt_client.is_connected():
        try:
            result = mqtt_client.publish(topic, payload, qos=qos, retain=retain)
            logger.info(f"📤 Published to {topic}: {payload}")
            return result
        except Exception as e:
            logger.error(f"❌ Failed to publish: {e}")
            return None
    else:
        logger.warning("⚠️ MQTT client not connected")
        return None