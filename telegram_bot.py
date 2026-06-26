import os
import json
import logging
import time
import threading
import requests
import paho.mqtt.client as mqtt

# Configuration from env
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
MQTT_BROKER = os.environ.get('MQTT_BROKER', 'broker.hivemq.com')
MQTT_PORT = int(os.environ.get('MQTT_PORT', 1883))
MQTT_TOPIC_CMDS = os.environ.get('MQTT_TOPIC_CMDS', 'invernadero/orellanas/cmd')
MQTT_TOPIC_ALERTS = os.environ.get('MQTT_TOPIC_ALERTS', 'invernadero/orellanas/alertas')
MQTT_TOPIC_TELEMETRY = os.environ.get('MQTT_TOPIC_TELEMETRY', 'invernadero/orellanas')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Available commands (must match those accepted by main.py)
COMMANDS = [
    'auto',
    'bomba_on',
    'bomba_off',
    'vent_on',
    'vent_off',
    'vent_auto',
    'incubacion',
    'primordios',
    'fructificacion',
    'estado',
    'actualizar', # 👈 Confirmado en la lista oficial de comandos permitidos
]

BASE_URL = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}'
known_chats = set()
state_request = {'event': None, 'payload': None, 'processed': False}

mqtt_client = mqtt.Client()


def send_message(chat_id, text, keyboard=None):
    payload = {
        'chat_id': chat_id,
        'text': text,
    }
    if keyboard:
        payload['reply_markup'] = {
            'keyboard': keyboard,
            'resize_keyboard': True,
            'one_time_keyboard': False,
        }

    resp = requests.post(f'{BASE_URL}/sendMessage', json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def build_keyboard():
    return [COMMANDS[i:i+3] for i in range(0, len(COMMANDS), 3)]


def publish_command(command):
    try:
        mqtt_client.publish(MQTT_TOPIC_CMDS, command)
        return True
    except Exception:
        logger.warning('MQTT publish failed, attempting reconnect...')
        try:
            mqtt_client.reconnect()
        except Exception:
            try:
                mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
            except Exception as e:
                logger.exception('MQTT reconnect failed: %s', e)
                return False

        try:
            mqtt_client.publish(MQTT_TOPIC_CMDS, command)
            return True
        except Exception as e:
            logger.exception('MQTT publish failed after reconnect: %s', e)
            return False


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info('MQTT connected, suscribing to alerts and telemetry')
        client.subscribe([(MQTT_TOPIC_ALERTS, 0), (MQTT_TOPIC_TELEMETRY, 0)])
    else:
        logger.warning('MQTT connect returned code %s', rc)


def on_message(client, userdata, msg):
    payload = msg.payload.decode('utf-8', errors='replace')
    topic = msg.topic

    if topic == MQTT_TOPIC_ALERTS:
        logger.info('Alert received: %s', payload)
        for chat_id in list(known_chats):
            try:
                send_message(chat_id, f'ALERTA: {payload}')
            except Exception as e:
                logger.exception('Failed sending alert to chat %s: %s', chat_id, e)

    elif topic == MQTT_TOPIC_TELEMETRY and state_request['event'] is not None and not state_request['processed']:
        state_request['payload'] = payload
        state_request['processed'] = True
        state_request['event'].set()


def handle_update(update):
    message = update.get('message') or update.get('edited_message')
    if not message:
        return

    chat_id = message['chat']['id']
    text = message.get('text', '').strip().lower()
    if not text:
        return

    # ⚡ Sanitizado: Permite limpiar comandos tipo /actualizar o /estado escritos a mano
    if text.startswith('/'):
        text = text[1:]
        if '@' in text:
            text = text.split('@', 1)[0]

    known_chats.add(chat_id)

    if text == 'start':
        keyboard = build_keyboard()
        help_text = 'Comandos disponibles:\n' + '\n'.join(f'- {c}' for c in COMMANDS)
        send_message(chat_id, help_text, keyboard)
        return

    if text == 'estado':
        success = publish_command('estado')
        if not success:
            send_message(chat_id, 'Error enviando comando de estado al dispositivo.')
            return

        event = threading.Event()
        state_request['event'] = event
        state_request['payload'] = None
        state_request['processed'] = False

        if event.wait(timeout=12):
            send_message(chat_id, f'Respuesta de estado:\n{state_request["payload"]}')
        else:
            send_message(chat_id, 'No se obtuvo respuesta de estado en 12 segundos.')

        state_request['event'] = None
        state_request['payload'] = None
        state_request['processed'] = False
        return

    # 📥 Procesador unificado para comandos estándar y el desencadenador del OTA
    if text in COMMANDS:
        success = publish_command(text)
        if success:
            if text == 'actualizar':
                send_message(chat_id, '📥 Orden de actualización enviada al ESP32 por MQTT. Comprobando GitHub...')
            else:
                send_message(chat_id, f'Comando enviado con éxito: {text}')
        else:
            send_message(chat_id, 'Error enviando comando al dispositivo.')
    else:
        send_message(chat_id, 'Comando no reconocido. Usa /start para ver la lista.')


def get_updates(offset=None, timeout=30):
    params = {'timeout': timeout}
    if offset:
        params['offset'] = offset

    resp = requests.get(f'{BASE_URL}/getUpdates', params=params, timeout=timeout + 10)
    resp.raise_for_status()
    return resp.json()


def main():
    if not TELEGRAM_TOKEN:
        logger.error('TELEGRAM_TOKEN no configurado. Exporta la variable de entorno.')
        return

    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        logger.exception("Could not start MQTT client: %s", e)

    offset = None
    logger.info("Starting Telegram polling bot")

    while True:
        try:
            response = get_updates(offset=offset)

            for update in response.get("result", []):
                offset = update["update_id"] + 1
                handle_update(update)

        except requests.HTTPError as e:
            logger.error("Status Code: %s", e.response.status_code)
            logger.error("Response Body: %s", e.response.text)
            time.sleep(5)

        except Exception as e:
            logger.exception("Unexpected error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
