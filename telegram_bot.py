import os
import json
import logging
import time
import requests
import paho.mqtt.client as mqtt

# Configuration from env
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
MQTT_BROKER = os.environ.get('MQTT_BROKER', 'broker.hivemq.com')
MQTT_PORT = int(os.environ.get('MQTT_PORT', 1883))
MQTT_TOPIC_CMDS = os.environ.get('MQTT_TOPIC_CMDS', 'invernadero/orellanas/cmd')

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
    'actualizar'
]

BASE_URL = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}'

# Create a simple MQTT client used to publish commands
mqtt_client = mqtt.Client()
try:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
except Exception as e:
    logger.warning('Could not connect MQTT at startup: %s', e)


def send_message(chat_id, text, keyboard=None):
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'MarkdownV2',
    }
    if keyboard:
        payload['reply_markup'] = json.dumps({'keyboard': keyboard, 'resize_keyboard': True, 'one_time_keyboard': False})

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


def handle_update(update):
    message = update.get('message') or update.get('edited_message')
    if not message:
        return

    chat_id = message['chat']['id']
    text = message.get('text', '').strip().lower()
    if not text:
        return

    # Si el usuario escribe con barra inclinada (ej: /actualizar o /estado), se la quitamos
    if text.startswith('/'):
        text = text[1:]

    if text == 'start':
        keyboard = build_keyboard()
        send_message(chat_id, 'Comandos disponibles:', keyboard)
        return

    if text in COMMANDS:
        success = publish_command(text)
        if success:
            # Mensaje amigable de confirmación para el usuario en Telegram
            if text == 'actualizar':
                send_message(chat_id, '📥 Orden de actualización enviada al ESP32 via MQTT. Comprobando GitHub...')
            else:
                send_message(chat_id, f'Comando enviado: {text}')
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

    offset = None
    logger.info('Starting Telegram polling bot')
    while True:
        try:
            response = get_updates(offset=offset)
            for update in response.get('result', []):
                offset = update['update_id'] + 1
                handle_update(update)
        except requests.HTTPError as e:
            logger.exception('Telegram API error: %s', e)
            time.sleep(5)
        except Exception as e:
            logger.exception('Unexpected error: %s', e)
            time.sleep(5)


if __name__ == '__main__':
    main()
