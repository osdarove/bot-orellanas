import os
import time
import logging
import threading
import requests
import paho.mqtt.client as mqtt

# ==========================
# CONFIGURACIÓN
# ==========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

MQTT_BROKER = os.getenv("MQTT_BROKER", "broker.hivemq.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

MQTT_TOPIC_CMDS = os.getenv(
    "MQTT_TOPIC_CMDS",
    "invernadero/orellanas/cmd"
)

MQTT_TOPIC_ALERTS = os.getenv(
    "MQTT_TOPIC_ALERTS",
    "invernadero/orellanas/alertas"
)

MQTT_TOPIC_TELEMETRY = os.getenv(
    "MQTT_TOPIC_TELEMETRY",
    "invernadero/orellanas"
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

COMMANDS = [
    "auto",
    "bomba_on",
    "bomba_off",
    "vent_on",
    "vent_off",
    "vent_auto",
    "incubacion",
    "primordios",
    "fructificacion",
    "estado",
    "actualizar",
]

known_chats = set()

state_request = {
    "event": None,
    "payload": None,
    "processed": False,
}

# API moderna de paho
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)


# ==========================
# TELEGRAM
# ==========================

def send_message(chat_id, text, keyboard=None):

    payload = {
        "chat_id": chat_id,
        "text": text
    }

    if keyboard:
        payload["reply_markup"] = {
            "keyboard": keyboard,
            "resize_keyboard": True,
            "one_time_keyboard": False
        }

    r = requests.post(
        f"{BASE_URL}/sendMessage",
        json=payload,
        timeout=15
    )

    r.raise_for_status()


def build_keyboard():
    return [
        COMMANDS[i:i + 3]
        for i in range(0, len(COMMANDS), 3)
    ]


def get_updates(offset=None, timeout=25):

    params = {
        "timeout": timeout,
        "allowed_updates": ["message"]
    }

    if offset is not None:
        params["offset"] = offset

    r = requests.get(
        f"{BASE_URL}/getUpdates",
        params=params,
        timeout=timeout + 10
    )

    r.raise_for_status()

    return r.json()


# ==========================
# MQTT
# ==========================

def publish_command(command):

    try:
        mqtt_client.publish(MQTT_TOPIC_CMDS, command)
        return True

    except Exception:

        logger.warning("MQTT reconectando...")

        try:
            mqtt_client.reconnect()
        except Exception:
            mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

        mqtt_client.publish(MQTT_TOPIC_CMDS, command)

        return True


def on_connect(client, userdata, flags, reason_code, properties):

    logger.info("MQTT conectado")

    client.subscribe(MQTT_TOPIC_ALERTS)
    client.subscribe(MQTT_TOPIC_TELEMETRY)


def on_message(client, userdata, msg):

    payload = msg.payload.decode(errors="ignore")

    if msg.topic == MQTT_TOPIC_ALERTS:

        logger.info("Alerta: %s", payload)

        for chat in list(known_chats):
            try:
                send_message(chat, f"🚨 {payload}")
            except Exception:
                logger.exception("No se pudo enviar alerta")

    elif (
        msg.topic == MQTT_TOPIC_TELEMETRY
        and state_request["event"] is not None
        and not state_request["processed"]
    ):

        state_request["payload"] = payload
        state_request["processed"] = True
        state_request["event"].set()


# ==========================
# COMANDOS
# ==========================

def handle_update(update):

    message = update.get("message")

    if not message:
        return

    chat_id = message["chat"]["id"]

    text = message.get("text", "").lower().strip()

    if text.startswith("/"):
        text = text[1:].split("@")[0]

    known_chats.add(chat_id)

    if text == "start":

        send_message(
            chat_id,
            "Comandos disponibles:",
            build_keyboard()
        )

        return

    if text == "estado":

        if not publish_command("estado"):
            send_message(chat_id, "No fue posible enviar el comando.")
            return

        event = threading.Event()

        state_request["event"] = event
        state_request["payload"] = None
        state_request["processed"] = False

        if event.wait(12):

            send_message(
                chat_id,
                state_request["payload"]
            )

        else:

            send_message(
                chat_id,
                "Tiempo agotado esperando respuesta."
            )

        state_request["event"] = None
        state_request["payload"] = None
        state_request["processed"] = False

        return

    if text in COMMANDS:

        if publish_command(text):

            send_message(
                chat_id,
                f"✅ {text}"
            )

        else:

            send_message(
                chat_id,
                "Error publicando comando."
            )

        return

    send_message(
        chat_id,
        "Comando no reconocido."
    )


# ==========================
# MAIN
# ==========================

def main():

    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN no configurado")

    try:
        requests.post(
            f"{BASE_URL}/deleteWebhook",
            json={
                "drop_pending_updates": False
            },
            timeout=10
        )
    except Exception:
        pass

    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    mqtt_client.connect(
        MQTT_BROKER,
        MQTT_PORT,
        60
    )

    mqtt_client.loop_start()

    logger.info("Telegram iniciado")

    offset = None

    while True:

        try:

            response = get_updates(offset)

            for update in response.get("result", []):

                offset = update["update_id"] + 1

                handle_update(update)

        except requests.HTTPError as e:

            logger.error(
                "Telegram HTTP %s",
                e.response.status_code
            )

            logger.error(e.response.text)

            if e.response.status_code == 409:
                logger.warning(
                    "409 detectado. Esperando 35 segundos..."
                )
                time.sleep(35)
            else:
                time.sleep(5)

        except Exception:

            logger.exception("Error inesperado")

            time.sleep(5)


if __name__ == "__main__":
    main()
