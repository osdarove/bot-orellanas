"""
Cliente MQTT que corre en un hilo de fondo dentro del backend.

Se conecta al mismo broker que usa el ESP32 (broker.hivemq.com),
se suscribe a los tópicos de telemetría y alertas, y guarda cada
mensaje recibido en PostgreSQL. También expone una función para
publicar comandos hacia el ESP32 desde la API.
"""

import os
import json
import threading
import time
import paho.mqtt.client as mqtt

from database import SessionLocal
from models import Telemetria, Alerta

# =====================================================================
# CONFIGURACIÓN MQTT (debe coincidir con la del ESP32 / main.py)
# =====================================================================
MQTT_BROKER = os.getenv("MQTT_BROKER", "broker.hivemq.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "orellanas-backend")

TOPICO_TELEMETRIA = "invernadero/orellanas"
TOPICO_COMANDOS = "invernadero/orellanas/cmd"
TOPICO_ALERTAS = "invernadero/orellanas/alertas"

# Último estado recibido, guardado en memoria para respuestas rápidas
# sin tener que consultar la base de datos en cada request.
ultimo_estado = {}
estado_lock = threading.Lock()

_cliente_mqtt = None


def _guardar_telemetria(data: dict):
    """Inserta una fila de telemetría en la base de datos."""
    db = SessionLocal()
    try:
        registro = Telemetria(
            etapa=data.get("etapa"),
            temp1=data.get("temp1"),
            hum1=data.get("hum1"),
            temp2=data.get("temp2"),
            hum2=data.get("hum2"),
            temp_promedio=data.get("temp_promedio"),
            hum_promedio=data.get("hum_promedio"),
            hum_control=data.get("hum_control"),
            bomba=data.get("bomba"),
            ventilador=data.get("ventilador"),
            modo=data.get("modo"),
            tanque=data.get("tanque"),
            minutos_bomba_hoy=data.get("minutos_bomba_hoy"),
        )
        db.add(registro)
        db.commit()
    except Exception as e:
        print("[DB] Error guardando telemetría:", e)
        db.rollback()
    finally:
        db.close()


def _guardar_alerta(data: dict, payload_crudo: str):
    """Inserta una fila de alerta en la base de datos."""
    db = SessionLocal()
    try:
        registro = Alerta(
            evento=data.get("evento"),
            mensaje=data.get("mensaje"),
            payload_crudo=payload_crudo,
        )
        db.add(registro)
        db.commit()
    except Exception as e:
        print("[DB] Error guardando alerta:", e)
        db.rollback()
    finally:
        db.close()


def _on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("[MQTT] Conectado al broker. Suscribiendo tópicos...")
        client.subscribe(TOPICO_TELEMETRIA)
        client.subscribe(TOPICO_ALERTAS)
    else:
        print("[MQTT] Falló la conexión, código:", rc)


def _on_message(client, userdata, msg):
    global ultimo_estado
    try:
        payload_texto = msg.payload.decode("utf-8")
        data = json.loads(payload_texto)
    except Exception as e:
        print("[MQTT] No se pudo parsear el mensaje JSON:", e)
        return

    if msg.topic == TOPICO_TELEMETRIA:
        with estado_lock:
            ultimo_estado = data
        _guardar_telemetria(data)

    elif msg.topic == TOPICO_ALERTAS:
        print("[ALERTA] Recibida:", data)
        _guardar_alerta(data, payload_texto)


def iniciar_cliente_mqtt():
    """
    Crea el cliente MQTT, conecta al broker y arranca el loop de
    red en un hilo separado para no bloquear el servidor FastAPI.
    Incluye reconexión automática manejada por paho-mqtt.
    """
    global _cliente_mqtt

    cliente = mqtt.Client(
        client_id=MQTT_CLIENT_ID,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    cliente.on_connect = _on_connect
    cliente.on_message = _on_message

    # reconnect_delay_set habilita backoff automático de reconexión
    cliente.reconnect_delay_set(min_delay=1, max_delay=30)

    conectado = False
    intentos = 0
    while not conectado and intentos < 10:
        try:
            cliente.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            conectado = True
        except Exception as e:
            intentos += 1
            print("[MQTT] Error conectando ({}). Reintentando en 3s...".format(e))
            time.sleep(3)

    if not conectado:
        print("[MQTT] No se pudo conectar tras varios intentos. "
              "El hilo seguirá reintentando en segundo plano.")

    # loop_start corre la red en un hilo aparte de forma indefinida
    cliente.loop_start()
    _cliente_mqtt = cliente
    return cliente


def publicar_comando(comando: str) -> bool:
    """
    Publica un comando en el tópico invernadero/orellanas/cmd para
    que el ESP32 lo reciba (auto, bomba_on, bomba_off, vent_on, ...).
    Retorna True si se pudo publicar.
    """
    if _cliente_mqtt is None:
        return False
    try:
        _cliente_mqtt.publish(TOPICO_COMANDOS, comando)
        return True
    except Exception as e:
        print("[MQTT] Error publicando comando:", e)
        return False


def obtener_ultimo_estado() -> dict:
    """Retorna en memoria el último JSON de telemetría recibido."""
    with estado_lock:
        return dict(ultimo_estado)
