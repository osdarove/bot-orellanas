import json
import os
import threading

import paho.mqtt.client as mqtt

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes
)

# ==========================
# CONFIG
# ==========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

MQTT_BROKER = os.getenv(
    "MQTT_BROKER",
    "broker.hivemq.com"
)

MQTT_PORT = 1883

TOPICO_CMD = "invernadero/orellanas/cmd"
TOPICO_DATA = "invernadero/orellanas"
TOPICO_ALERTAS = "invernadero/orellanas/alertas"

chat_ids = set()

ultimo_estado = {}

# ==========================
# MQTT
# ==========================

mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2
)

# ==========================
# ENVIAR COMANDO
# ==========================

def enviar_comando(cmd):

    mqtt_client.publish(
        TOPICO_CMD,
        cmd
    )

# ==========================
# MQTT MESSAGE
# ==========================

async_app = None

def on_message(client, userdata, msg):

    global ultimo_estado

    try:

        payload = msg.payload.decode()

        if msg.topic == TOPICO_DATA:

            ultimo_estado = json.loads(payload)

        elif msg.topic == TOPICO_ALERTAS:

            alerta = json.loads(payload)

            mensaje = (
                "🚨 ALERTA\n\n"
                + json.dumps(
                    alerta,
                    indent=2,
                    ensure_ascii=False
                )
            )

            for chat_id in chat_ids:

                async_app.bot.send_message(
                    chat_id=chat_id,
                    text=mensaje
                )

    except Exception as e:

        print("MQTT ERROR:", e)

# ==========================
# MQTT START
# ==========================

def iniciar_mqtt():

    mqtt_client.on_message = on_message

    mqtt_client.connect(
        MQTT_BROKER,
        MQTT_PORT,
        60
    )

    mqtt_client.subscribe(TOPICO_DATA)
    mqtt_client.subscribe(TOPICO_ALERTAS)

    mqtt_client.loop_forever()

# ==========================
# TELEGRAM
# ==========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_ids.add(update.effective_chat.id)

    await update.message.reply_text(
        "Bot Orellanas conectado."
    )

# ==========================
# ESTADO
# ==========================

async def estado(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    enviar_comando("estado")

    await update.message.reply_text(
        json.dumps(
            ultimo_estado,
            indent=2,
            ensure_ascii=False
        )
    )

# ==========================
# BOMBA
# ==========================

async def bomba_on(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    enviar_comando("bomba_on")

    await update.message.reply_text(
        "💧 Bomba ON"
    )

async def bomba_off(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    enviar_comando("bomba_off")

    await update.message.reply_text(
        "💧 Bomba OFF"
    )

async def auto(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    enviar_comando("auto")

    await update.message.reply_text(
        "Modo automático"
    )

# ==========================
# ETAPAS
# ==========================

async def incubacion(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    enviar_comando("incubacion")

    await update.message.reply_text(
        "🌱 Incubación"
    )

async def primordios(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    enviar_comando("primordios")

    await update.message.reply_text(
        "🍄 Primordios"
    )

async def fructificacion(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    enviar_comando("fructificacion")

    await update.message.reply_text(
        "🍄 Fructificación"
    )

# ==========================
# VENTILADOR
# ==========================

async def vent_on(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    enviar_comando("vent_on")

    await update.message.reply_text(
        "🌬 Ventilador ON"
    )

async def vent_off(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    enviar_comando("vent_off")

    await update.message.reply_text(
        "🌬 Ventilador OFF"
    )

async def vent_auto(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    enviar_comando("vent_auto")

    await update.message.reply_text(
        "🌬 Ventilador AUTO"
    )

# ==========================
# MAIN
# ==========================

def main():

    global async_app

    async_app = Application.builder()\
        .token(TELEGRAM_TOKEN)\
        .build()

    async_app.add_handler(
        CommandHandler("start", start)
    )

    async_app.add_handler(
        CommandHandler("estado", estado)
    )

    async_app.add_handler(
        CommandHandler("bomba_on", bomba_on)
    )

    async_app.add_handler(
        CommandHandler("bomba_off", bomba_off)
    )

    async_app.add_handler(
        CommandHandler("auto", auto)
    )

    async_app.add_handler(
        CommandHandler(
            "incubacion",
            incubacion
        )
    )

    async_app.add_handler(
        CommandHandler(
            "primordios",
            primordios
        )
    )

    async_app.add_handler(
        CommandHandler(
            "fructificacion",
            fructificacion
        )
    )

    async_app.add_handler(
        CommandHandler(
            "vent_on",
            vent_on
        )
    )

    async_app.add_handler(
        CommandHandler(
            "vent_off",
            vent_off
        )
    )

    async_app.add_handler(
        CommandHandler(
            "vent_auto",
            vent_auto
        )
    )

    threading.Thread(
        target=iniciar_mqtt,
        daemon=True
    ).start()

    print("Telegram Bot iniciado")

    async_app.run_polling()

if __name__ == "__main__":
    main()
