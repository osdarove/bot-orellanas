"""
Bot de Telegram — Invernadero Orellanas
Despliega en Railway o Render (gratis)
"""
import os, json, threading
import paho.mqtt.client as mqtt
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ── Env vars (configura en Railway) ──────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["8653215387:AAFNlm994JvJ3b1QwrPcpYfD5gR4NFkvXLU"]
MQTT_BROKER    = "broker.hivemq.com"
MQTT_PORT      = 1883
TOPIC_DATA     = "invernadero/orellanas"
TOPIC_CMD      = "invernadero/orellanas/cmd"

# ── Estado compartido ─────────────────────────────────────────────────────────
ultimo: dict = {}
app_tg = None

# ─────────────────────────────────────────────────────────────────────────────
# MQTT
# ─────────────────────────────────────────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    print(f"✅ MQTT conectado (rc={rc})")
    client.subscribe(TOPIC_DATA)

def on_message(client, userdata, msg):
    global ultimo
    try:
        ultimo = json.loads(msg.payload.decode())
        print("📥", ultimo)
    except Exception as e:
        print("Error MQTT msg:", e)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def icono_bomba(estado): return "💧 ON" if estado == "ON" else "⛔ OFF"
def icono_modo(modo):    return "🤖 Auto" if modo == "auto" else "🖐 Manual"

def resumen():
    if not ultimo:
        return "⏳ Sin datos aún. Usa /leer"
    return (
        f"🌡️ Temp:    S1 `{ultimo.get('temp1')}°C`  S2 `{ultimo.get('temp2')}°C`  Prom `{ultimo.get('temp_promedio')}°C`\n"
        f"💧 Humedad: S1 `{ultimo.get('hum1')}%`   S2 `{ultimo.get('hum2')}%`   Prom `{ultimo.get('hum_promedio')}%`\n"
        f"🔌 Bomba:   {icono_bomba(ultimo.get('bomba','?'))}\n"
        f"⚙️ Modo:    {icono_modo(ultimo.get('modo','?'))}"
    )

# ─────────────────────────────────────────────────────────────────────────────
# Comandos Telegram
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍄 *Bot Invernadero Orellanas*\n\n"
        "/datos     — Ver temperatura y humedad\n"
        "/bomba\\_on  — Encender bomba manualmente\n"
        "/bomba\\_off — Apagar bomba manualmente\n"
        "/auto      — Volver a control automático\n"
        "/leer      — Pedir lectura inmediata\n"
        "/estado    — Estado de conexión",
        parse_mode="Markdown"
    )

async def cmd_datos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(resumen(), parse_mode="Markdown")

async def cmd_bomba_on(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mqtt_client.publish(TOPIC_CMD, "bomba_on")
    await update.message.reply_text("💧 Bomba *encendida* (modo manual)", parse_mode="Markdown")

async def cmd_bomba_off(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mqtt_client.publish(TOPIC_CMD, "bomba_off")
    await update.message.reply_text("⛔ Bomba *apagada* (modo manual)", parse_mode="Markdown")

async def cmd_auto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mqtt_client.publish(TOPIC_CMD, "auto")
    await update.message.reply_text(
        f"🤖 Modo *automático* activado\n"
        f"La bomba se controla por humedad ({80}%–{93}%)",
        parse_mode="Markdown"
    )

async def cmd_leer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mqtt_client.publish(TOPIC_CMD, "leer")
    await update.message.reply_text("📡 Solicitando lectura al ESP32...")

async def cmd_estado(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ok = mqtt_client.is_connected()
    await update.message.reply_text(
        f"{'✅' if ok else '❌'} MQTT: {'conectado' if ok else 'desconectado'}\n"
        + resumen(),
        parse_mode="Markdown"
    )

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mqtt_client = mqtt.Client()
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    threading.Thread(target=mqtt_client.loop_forever, daemon=True).start()

    app_tg = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    for cmd, fn in [
        ("start",     cmd_start),
        ("datos",     cmd_datos),
        ("bomba_on",  cmd_bomba_on),
        ("bomba_off", cmd_bomba_off),
        ("auto",      cmd_auto),
        ("leer",      cmd_leer),
        ("estado",    cmd_estado),
    ]:
        app_tg.add_handler(CommandHandler(cmd, fn))

    print("🍄 Bot Orellanas iniciado...")
    app_tg.run_polling()
