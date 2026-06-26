#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telegram_bot.py

Puente Telegram <-> MQTT (HiveMQ) <-> ESP32 para control de invernadero de orellanas.

Arquitectura:

    Telegram
        |
        v
  telegram_bot.py
        |
   MQTT (HiveMQ)
        |
        v
      ESP32

Todo el control y la telemetría viajan por MQTT. Telegram solo se usa como
interfaz de entrada/salida para el usuario (polling, sin webhook).

Tecnologías permitidas: requests, paho-mqtt, threading, json, logging, time, os.
No se usan frameworks adicionales (no Flask, no python-telegram-bot, etc).

Listo para ejecutar en Railway. Variables de entorno requeridas:
    TELEGRAM_TOKEN   -> token del bot de Telegram (obligatorio)

Variables de entorno opcionales:
    MQTT_BROKER      -> default: broker.hivemq.com
    MQTT_PORT        -> default: 1883
    MQTT_TOPIC_CMD   -> default: invernadero/orellanas/cmd
    MQTT_TOPIC_EVT   -> default: invernadero/orellanas/eventos
    ESTADO_TIMEOUT_S -> default: 12
"""

import os
import json
import time
import logging
import threading

import requests
import paho.mqtt.client as mqtt


# =========================================================================
# CONFIGURACION
# =========================================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()

MQTT_BROKER = os.environ.get("MQTT_BROKER", "broker.hivemq.com").strip()
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

MQTT_TOPIC_CMD = os.environ.get("MQTT_TOPIC_CMD", "invernadero/orellanas/cmd").strip()
MQTT_TOPIC_EVT = os.environ.get("MQTT_TOPIC_EVT", "invernadero/orellanas/eventos").strip()

ESTADO_TIMEOUT_S = float(os.environ.get("ESTADO_TIMEOUT_S", "12"))

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"

# Topic antiguo que NO debe usarse nunca (se deja documentado por claridad,
# no se referencia en ningun publish/subscribe del código):
#   invernadero/orellanas

COMANDOS_VALIDOS = {
    "estado",
    "actualizar",
    "auto",
    "incubacion",
    "primordios",
    "fructificacion",
    "bomba_on",
    "bomba_off",
    "vent_on",
    "vent_off",
}

TECLADO_BOTONES = [
    ["estado", "actualizar"],
    ["auto", "incubacion"],
    ["primordios", "fructificacion"],
    ["bomba_on", "bomba_off"],
    ["vent_on", "vent_off"],
]

MENSAJE_BIENVENIDA = "Comandos disponibles"

LONG_POLL_TIMEOUT_S = 30  # timeout del long-polling de getUpdates
CONFLICT_WAIT_S = 35      # espera tras un 409 antes de reintentar deleteWebhook
HEARTBEAT_LOG_EVERY_S = 300  # cada cuanto loggear que seguimos recibiendo heartbeats


# =========================================================================
# LOGGING
# =========================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("telegram_bot")


# =========================================================================
# ESTADO COMPARTIDO EN MEMORIA
# =========================================================================

class EstadoGlobal:
    """
    Contenedor de estado compartido entre el hilo de Telegram y el hilo de MQTT.
    Protegido por un lock porque ambos hilos leen/escriben sobre él.
    """

    def __init__(self):
        self.lock = threading.Lock()

        # Chats de Telegram conocidos (a los que se les puede notificar).
        self.known_chats = set()

        # Marca de tiempo del último heartbeat recibido del ESP32.
        self.ultimo_heartbeat = None

        # Evento usado para esperar la respuesta al comando "estado".
        self.estado_event = threading.Event()
        self.estado_payload = None

    def agregar_chat(self, chat_id):
        with self.lock:
            nuevo = chat_id not in self.known_chats
            self.known_chats.add(chat_id)
        if nuevo:
            logger.info("Nuevo chat registrado: %s", chat_id)

    def listar_chats(self):
        with self.lock:
            return list(self.known_chats)

    def set_heartbeat(self):
        with self.lock:
            self.ultimo_heartbeat = time.time()

    def get_heartbeat(self):
        with self.lock:
            return self.ultimo_heartbeat

    def preparar_espera_estado(self):
        with self.lock:
            self.estado_payload = None
            self.estado_event.clear()

    def resolver_estado(self, payload):
        with self.lock:
            self.estado_payload = payload
            self.estado_event.set()

    def esperar_estado(self, timeout_s):
        llego = self.estado_event.wait(timeout=timeout_s)
        with self.lock:
            payload = self.estado_payload
        return llego, payload


estado_global = EstadoGlobal()


# =========================================================================
# CLIENTE TELEGRAM (HTTP / polling, sin webhook)
# =========================================================================

class TelegramClient:
    """
    Cliente minimalista de la API de Telegram basado en requests + polling
    (getUpdates). No usa webhooks. Maneja explícitamente el error 409.
    """

    def __init__(self, token):
        if not token:
            raise RuntimeError(
                "TELEGRAM_TOKEN no está definido. Configúralo como variable de entorno."
            )
        self.token = token
        self.base_url = TELEGRAM_API_BASE.format(token=token)
        self.offset = 0
        self.session = requests.Session()

    def delete_webhook(self):
        """
        Elimina cualquier webhook configurado. Debe ejecutarse al iniciar
        y también cada vez que se reciba un 409 Conflict.
        """
        url = f"{self.base_url}/deleteWebhook"
        try:
            resp = self.session.post(url, params={"drop_pending_updates": False}, timeout=10)
            if resp.status_code == 200:
                logger.info("deleteWebhook ejecutado correctamente.")
            else:
                logger.warning(
                    "deleteWebhook devolvió status %s: %s", resp.status_code, resp.text
                )
        except requests.RequestException as exc:
            logger.error("Error HTTP ejecutando deleteWebhook: %s", exc)

    def get_updates(self):
        """
        Hace long-polling sobre getUpdates. Devuelve la lista de updates
        (puede ser vacía). Lanza Conflict409Error si Telegram responde 409.
        """
        url = f"{self.base_url}/getUpdates"
        params = {
            "offset": self.offset,
            "timeout": LONG_POLL_TIMEOUT_S,
        }
        try:
            resp = self.session.get(
                url, params=params, timeout=LONG_POLL_TIMEOUT_S + 10
            )
        except requests.RequestException as exc:
            logger.error("Error HTTP en getUpdates: %s", exc)
            time.sleep(3)
            return []

        if resp.status_code == 409:
            raise Conflict409Error()

        if resp.status_code != 200:
            logger.error(
                "getUpdates devolvió status %s: %s", resp.status_code, resp.text
            )
            time.sleep(3)
            return []

        try:
            data = resp.json()
        except ValueError as exc:
            logger.error("Error decodificando JSON de getUpdates: %s", exc)
            return []

        if not data.get("ok", False):
            logger.error("getUpdates respondió ok=false: %s", data)
            return []

        result = data.get("result", [])
        if result:
            self.offset = result[-1]["update_id"] + 1
        return result

    def send_message(self, chat_id, text, with_keyboard=False):
        """
        Envía un mensaje de texto. Si with_keyboard=True, adjunta el
        teclado permanente de comandos.
        """
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        if with_keyboard:
            payload["reply_markup"] = json.dumps(
                {
                    "keyboard": TECLADO_BOTONES,
                    "resize_keyboard": True,
                    "is_persistent": True,
                }
            )
        try:
            resp = self.session.post(url, data=payload, timeout=10)
            if resp.status_code == 409:
                raise Conflict409Error()
            if resp.status_code != 200:
                logger.error(
                    "sendMessage devolvió status %s: %s", resp.status_code, resp.text
                )
        except Conflict409Error:
            raise
        except requests.RequestException as exc:
            logger.error("Error HTTP en sendMessage: %s", exc)


class Conflict409Error(Exception):
    """Señala que Telegram respondió 409 Conflict (otra instancia haciendo polling)."""
    pass


# =========================================================================
# CLIENTE MQTT
# =========================================================================

class MqttBridge:
    """
    Envuelve el cliente paho-mqtt: maneja conexión, reconexión automática,
    publicación de comandos y despacho de eventos JSON recibidos del ESP32.
    """

    def __init__(self, broker, port, topic_cmd, topic_evt, on_evento):
        self.broker = broker
        self.port = port
        self.topic_cmd = topic_cmd
        self.topic_evt = topic_evt
        self.on_evento = on_evento

        self.client = mqtt.Client(
            client_id="",
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        self._connected = threading.Event()

    # -- ciclo de vida ----------------------------------------------------

    def start(self):
        self._connect_blocking()
        self.client.loop_start()

    def _connect_blocking(self):
        intento = 1
        while True:
            try:
                logger.info(
                    "Conectando a MQTT %s:%s (intento %s)...",
                    self.broker, self.port, intento,
                )
                self.client.connect(self.broker, self.port, keepalive=60)
                return
            except Exception as exc:
                logger.error("Error conectando a MQTT: %s", exc)
                intento += 1
                time.sleep(5)

    # -- callbacks paho -----------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected.set()
            logger.info("Conexión MQTT establecida con %s:%s", self.broker, self.port)
            client.subscribe(self.topic_evt)
            logger.info("Suscrito a topic de eventos: %s", self.topic_evt)
        else:
            self._connected.clear()
            logger.error("Fallo de conexión MQTT, rc=%s", rc)

    def _on_disconnect(self, client, userdata, rc):
        self._connected.clear()
        logger.warning("Desconectado de MQTT (rc=%s). Reconectando...", rc)
        threading.Thread(target=self._reconnect_loop, daemon=True).start()

    def _reconnect_loop(self):
        intento = 1
        while not self._connected.is_set():
            try:
                logger.info("Intentando reconexión MQTT (intento %s)...", intento)
                self.client.reconnect()
                return
            except Exception as exc:
                logger.error("Error en reconexión MQTT: %s", exc)
                intento += 1
                time.sleep(5)

    def _on_message(self, client, userdata, msg):
        try:
            texto = msg.payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            logger.error("Error decodificando payload MQTT: %s", exc)
            return

        try:
            data = json.loads(texto)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Error parseando JSON de MQTT (%s): %s", exc, texto)
            return

        try:
            self.on_evento(data)
        except Exception as exc:
            logger.error("Error procesando evento MQTT: %s", exc)

    # -- publicación --------------------------------------------------------

    def publicar_comando(self, comando):
        """
        Publica un comando en el topic de comandos. Si falla, intenta
        reconnect() -> connect() -> publish() según lo requerido.
        """
        try:
            result = self.client.publish(self.topic_cmd, comando, qos=1)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(f"publish devolvió rc={result.rc}")
            logger.info("Comando publicado en MQTT: %s", comando)
            return
        except Exception as exc:
            logger.error("Fallo al publicar comando '%s': %s. Reintentando...", comando, exc)

        # Plan de recuperación: reconnect() -> connect() -> publish()
        try:
            self.client.reconnect()
            logger.info("MQTT reconnect() exitoso, reintentando publish.")
            result = self.client.publish(self.topic_cmd, comando, qos=1)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.info("Comando publicado tras reconnect(): %s", comando)
                return
        except Exception as exc:
            logger.error("Fallo reconnect() para publicar '%s': %s", comando, exc)

        try:
            self.client.connect(self.broker, self.port, keepalive=60)
            logger.info("MQTT connect() exitoso, reintentando publish.")
            result = self.client.publish(self.topic_cmd, comando, qos=1)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.info("Comando publicado tras connect(): %s", comando)
                return
            else:
                logger.error("publish final falló con rc=%s para '%s'", result.rc, comando)
        except Exception as exc:
            logger.error("Fallo connect() para publicar '%s': %s", comando, exc)


# =========================================================================
# FORMATEO DE MENSAJES
# =========================================================================

def formatear_estado(data):
    """
    Construye el mensaje bonito de estado a partir del JSON del ESP32.
    Nunca se muestra el JSON crudo al usuario.

    Importante: cuando los sensores DHT22 del ESP32 fallan, el firmware
    envía null para temperatura/humedad. data.get(clave, default) solo
    usa el default si la clave NO EXISTE, pero aquí la clave sí existe
    con valor None, así que se normaliza explícitamente a "N/D".
    """
    temperatura = data.get("temperatura")
    humedad = data.get("humedad")
    bomba = data.get("bomba", None)
    ventilador = data.get("ventilador", None)
    modo = data.get("modo", "N/D")
    wifi = data.get("wifi")
    version = data.get("version", "N/D")

    temperatura_txt = "N/D" if temperatura is None else f"{temperatura}"
    humedad_txt = "N/D" if humedad is None else f"{humedad}"
    wifi_txt = "N/D" if wifi is None else f"{wifi}"

    bomba_txt = "Encendida" if bomba is True else ("Apagada" if bomba is False else "N/D")
    vent_txt = "Encendido" if ventilador is True else ("Apagado" if ventilador is False else "N/D")

    return (
        "🌡 Estado del Invernadero\n\n"
        f"🌡 Temperatura: {temperatura_txt} °C\n"
        f"💧 Humedad: {humedad_txt} %\n"
        f"🚿 Bomba: {bomba_txt}\n"
        f"🌀 Ventilador: {vent_txt}\n"
        f"🧠 Modo: {modo}\n"
        f"📡 WiFi: {wifi_txt} dBm\n"
        f"🔖 Firmware: {version}"
    )


def formatear_boot(data):
    version = data.get("version", "N/D")
    return f"🟢 ESP32 conectado.\n\nFirmware: {version}"


def formatear_ota(estado, mensaje):
    if estado == "pending":
        return "📥 Solicitud OTA recibida."
    if estado == "checking":
        return "🔍 Verificando firmware en GitHub..."
    if estado == "new_version":
        return "🆕 Nueva versión encontrada."
    if estado == "restart":
        return "♻ Reiniciando ESP32..."
    if estado == "up_to_date":
        return "✅ El firmware ya estaba actualizado."
    if estado == "error":
        texto_mensaje = mensaje or "Error desconocido."
        return f"❌ Error durante la actualización.\n\n{texto_mensaje}"
    # Estado OTA no reconocido: se informa de forma genérica sin mostrar JSON.
    texto_mensaje = mensaje or ""
    return f"ℹ️ Evento OTA: {estado}\n{texto_mensaje}".strip()


def formatear_alerta(mensaje):
    texto_mensaje = mensaje or "Alerta sin detalle."
    return f"🚨 ALERTA\n\n{texto_mensaje}"


# =========================================================================
# LÓGICA DE NEGOCIO: DESPACHO DE EVENTOS MQTT -> TELEGRAM
# =========================================================================

class Despachador:
    """
    Recibe los eventos JSON ya parseados desde MqttBridge y decide qué
    hacer: resolver la espera de "estado", notificar OTA, boot, heartbeat,
    error o alerta, enviando los mensajes correspondientes por Telegram.
    """

    def __init__(self, telegram_client, estado):
        self.tg = telegram_client
        self.estado = estado
        self._ultimo_log_heartbeat = 0

    def procesar_evento(self, data):
        tipo = data.get("tipo")

        if tipo == "estado":
            self._procesar_estado(data)
        elif tipo == "ota":
            self._procesar_ota(data)
        elif tipo == "boot":
            self._procesar_boot(data)
        elif tipo == "heartbeat":
            self._procesar_heartbeat(data)
        elif tipo == "error":
            self._procesar_error(data)
        elif tipo == "alerta":
            self._procesar_alerta(data)
        else:
            logger.info("Evento MQTT con tipo no reconocido: %s -> %s", tipo, data)

    def _procesar_estado(self, data):
        logger.info("Evento de estado recibido del ESP32.")
        self.estado.resolver_estado(data)

    def _procesar_ota(self, data):
        ota_estado = data.get("estado", "")
        mensaje = data.get("mensaje", "")
        logger.info("Evento OTA recibido: estado=%s mensaje=%s", ota_estado, mensaje)
        texto = formatear_ota(ota_estado, mensaje)
        self._broadcast(texto)

    def _procesar_boot(self, data):
        logger.info("Evento de boot recibido del ESP32: %s", data)
        texto = formatear_boot(data)
        self._broadcast(texto)

    def _procesar_heartbeat(self, data):
        self.estado.set_heartbeat()
        ahora = time.time()
        if ahora - self._ultimo_log_heartbeat > HEARTBEAT_LOG_EVERY_S:
            logger.info("Heartbeat recibido del ESP32 (alive).")
            self._ultimo_log_heartbeat = ahora
        # No se envía mensaje a Telegram para heartbeats, según requerimiento.

    def _procesar_error(self, data):
        mensaje = data.get("mensaje", "Error desconocido.")
        contexto = data.get("estado", "")
        logger.error("Evento de error MQTT recibido: estado=%s mensaje=%s", contexto, mensaje)
        # Los errores de tipo "comando" no están especificados para broadcast
        # automático, pero se registran siempre en el log para diagnóstico.

    def _procesar_alerta(self, data):
        mensaje = data.get("mensaje", "")
        logger.warning("Alerta MQTT recibida: %s", mensaje)
        texto = formatear_alerta(mensaje)
        self._broadcast(texto)

    def _broadcast(self, texto):
        chats = self.estado.listar_chats()
        if not chats:
            logger.info("Evento generado pero no hay chats registrados todavía: %s", texto)
            return
        for chat_id in chats:
            try:
                self.tg.send_message(chat_id, texto)
            except Conflict409Error:
                # Se propaga para que el loop principal de Telegram maneje el 409.
                raise
            except Exception as exc:
                logger.error("Error enviando mensaje a chat %s: %s", chat_id, exc)


# =========================================================================
# MANEJO DE COMANDOS DE TELEGRAM
# =========================================================================

class ManejadorComandos:
    """
    Procesa los mensajes de texto recibidos desde Telegram y ejecuta la
    acción correspondiente (publicar comando MQTT, esperar estado, etc).
    """

    def __init__(self, telegram_client, mqtt_bridge, estado):
        self.tg = telegram_client
        self.mqtt = mqtt_bridge
        self.estado = estado

    def procesar_update(self, update):
        message = update.get("message")
        if not message:
            return

        chat = message.get("chat", {})
        chat_id = chat.get("id")
        if chat_id is None:
            return

        self.estado.agregar_chat(chat_id)

        texto = (message.get("text") or "").strip()
        if not texto:
            return

        if texto == "/start":
            self._comando_start(chat_id)
            return

        comando = texto.lower()

        if comando == "estado":
            self._comando_estado(chat_id)
            return

        if comando == "actualizar":
            self._comando_actualizar(chat_id)
            return

        if comando in COMANDOS_VALIDOS:
            self._comando_generico(chat_id, comando)
            return

        logger.info("Comando no reconocido recibido de chat %s: %s", chat_id, texto)
        self.tg.send_message(
            chat_id,
            "❓ Comando no reconocido. Usa el teclado disponible.",
        )

    def _comando_start(self, chat_id):
        logger.info("Comando /start recibido de chat %s", chat_id)
        self.tg.send_message(chat_id, MENSAJE_BIENVENIDA, with_keyboard=True)

    def _comando_estado(self, chat_id):
        logger.info("Comando 'estado' recibido de chat %s", chat_id)
        self.estado.preparar_espera_estado()
        self.mqtt.publicar_comando("estado")

        llego, payload = self.estado.esperar_estado(ESTADO_TIMEOUT_S)
        if llego and payload is not None:
            texto = formatear_estado(payload)
            self.tg.send_message(chat_id, texto)
        else:
            logger.warning("Timeout esperando respuesta de 'estado' del ESP32.")
            self.tg.send_message(
                chat_id,
                "⏱ No se recibió respuesta del ESP32 a tiempo. Intenta de nuevo.",
            )

    def _comando_actualizar(self, chat_id):
        logger.info("Comando 'actualizar' recibido de chat %s", chat_id)
        self.mqtt.publicar_comando("actualizar")
        self.tg.send_message(
            chat_id,
            "📥 Orden enviada al ESP32.\n\nEsperando respuesta...",
        )

    def _comando_generico(self, chat_id, comando):
        logger.info("Comando '%s' recibido de chat %s", comando, chat_id)
        self.mqtt.publicar_comando(comando)
        self.tg.send_message(chat_id, f"✅ Comando '{comando}' enviado al ESP32.")


# =========================================================================
# LOOP PRINCIPAL DE TELEGRAM (POLLING)
# =========================================================================

def loop_telegram(telegram_client, manejador):
    """
    Loop infinito de polling de Telegram. Maneja 409 Conflict según
    especificación: espera 35s, vuelve a ejecutar deleteWebhook y continúa,
    sin terminar el proceso.
    """
    logger.info("Iniciando loop de polling de Telegram...")
    telegram_client.delete_webhook()

    while True:
        try:
            updates = telegram_client.get_updates()
            for update in updates:
                try:
                    manejador.procesar_update(update)
                except Conflict409Error:
                    raise
                except Exception as exc:
                    logger.error("Error procesando update de Telegram: %s", exc)

        except Conflict409Error:
            logger.warning(
                "Telegram devolvió 409 Conflict. Esperando %s segundos antes de "
                "reintentar deleteWebhook...",
                CONFLICT_WAIT_S,
            )
            time.sleep(CONFLICT_WAIT_S)
            telegram_client.delete_webhook()
            continue

        except Exception as exc:
            logger.error("Error inesperado en loop de Telegram: %s", exc)
            time.sleep(5)
            continue


# =========================================================================
# PUNTO DE ENTRADA
# =========================================================================

def main():
    logger.info("Iniciando telegram_bot.py ...")
    logger.info("Broker MQTT: %s:%s", MQTT_BROKER, MQTT_PORT)
    logger.info("Topic comandos: %s", MQTT_TOPIC_CMD)
    logger.info("Topic eventos: %s", MQTT_TOPIC_EVT)

    telegram_client = TelegramClient(TELEGRAM_TOKEN)
    despachador = Despachador(telegram_client, estado_global)

    mqtt_bridge = MqttBridge(
        broker=MQTT_BROKER,
        port=MQTT_PORT,
        topic_cmd=MQTT_TOPIC_CMD,
        topic_evt=MQTT_TOPIC_EVT,
        on_evento=despachador.procesar_evento,
    )
    mqtt_bridge.start()

    manejador = ManejadorComandos(telegram_client, mqtt_bridge, estado_global)

    # El loop de Telegram corre en el hilo principal; MQTT corre en su
    # propio loop interno (paho loop_start ya lanzó un hilo).
    loop_telegram(telegram_client, manejador)


if __name__ == "__main__":
    main()
