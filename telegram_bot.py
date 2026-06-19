import os
import logging
import time

from telegram import ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
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
]

# Create a simple MQTT client used to publish commands
mqtt_client = mqtt.Client()
try:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
except Exception as e:
    logger.warning('Could not connect MQTT at startup: %s', e)


def start(update, context):
    """Handler for /start - show available commands with a keyboard."""
    text_lines = ["Comandos disponibles:\n"]
    for cmd in COMMANDS:
        text_lines.append(f"- {cmd}")
    text = "\n".join(text_lines)

    # Build keyboard: 3 columns
    keyboard = [COMMANDS[i:i+3] for i in range(0, len(COMMANDS), 3)]

    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=False, resize_keyboard=True)
    update.message.reply_text(text, reply_markup=reply_markup)


def handle_text(update, context):
    """When user sends a text (or presses keyboard), if it's a known command publish to MQTT."""
    text = update.message.text.strip().lower()
    user = update.effective_user

    if text in COMMANDS:
        # Try to publish; if publish fails, attempt reconnect once then retry
        try:
            mqtt_client.publish(MQTT_TOPIC_CMDS, text)
            update.message.reply_text(f"Comando enviado: {text}")
            logger.info('User %s sent command %s', user.username or user.id, text)
            return
        except Exception:
            logger.warning('MQTT publish failed, attempting reconnect...')
            try:
                mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
                mqtt_client.publish(MQTT_TOPIC_CMDS, text)
                update.message.reply_text(f"Comando enviado: {text}")
                logger.info('User %s sent command %s (after reconnect)', user.username or user.id, text)
                return
            except Exception as e:
                logger.exception('MQTT publish/reconnect failed: %s', e)
                update.message.reply_text('Error enviando comando al dispositivo.')
                return

    update.message.reply_text('Comando no reconocido. Usa /start para ver la lista.')


def main():
    if not TELEGRAM_TOKEN:
        logger.error('TELEGRAM_TOKEN no configurado. Exporta la variable de entorno.')
        return

    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler('start', start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    logger.info('Starting Telegram bot (polling)')
    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()
