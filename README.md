# Orellanas MQTT Telegram Bot

Este repositorio contiene el bot de Telegram que publica comandos al ESP32 a través de MQTT.

## Archivos incluidos

- `main.py` – código MicroPython para el ESP32.
- `telegram_bot.py` – bot de Telegram que muestra comandos con `/start` y envía mensajes al tópico MQTT `invernadero/orellanas/cmd`.
- `requirements.txt` – dependencias Python.
- `Procfile` – instrucción para Railway, ejecuta el bot como worker.
- `.gitignore` – ignorar binarios y directorios de entorno.

## Variables de entorno necesarias

Define estas variables en Railway / localmente:

- `TELEGRAM_TOKEN` – token del bot de Telegram.
- `MQTT_BROKER` – broker MQTT (por defecto `broker.hivemq.com`).
- `MQTT_PORT` – puerto MQTT (por defecto `1883`).
- `MQTT_TOPIC_CMDS` – tópico de comandos (por defecto `invernadero/orellanas/cmd`).

## Configuración local

1. Instala dependencias:
   ```bash
   python -m pip install -r requirements.txt
   ```
2. Define el token de Telegram:
   - PowerShell:
     ```powershell
     $env:TELEGRAM_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
     ```
3. Ejecuta el bot:
   ```bash
   python telegram_bot.py
   ```

## Despliegue en Railway

1. Crea un repositorio en GitHub y sube este proyecto.
2. En Railway, crea un nuevo proyecto y elige **Deploy from GitHub**.
3. Conecta tu repo y selecciona la rama `main`.
4. Configura las variables de entorno en Railway:
   - `TELEGRAM_TOKEN`
   - `MQTT_BROKER`
   - `MQTT_PORT`
   - `MQTT_TOPIC_CMDS`
5. Railway detectará Python y usará `Procfile` para ejecutar el bot como worker.

## Publicar comando de prueba

Puedes probar enviando un mensaje a tu bot y verificando que el ESP32 reciba el comando. Asegúrate de que el ESP32 está suscrito a `invernadero/orellanas/cmd`.

## Nota

`main.py` es el firmware MicroPython que debe permanecer en el ESP32. El bot en Railway solo envía comandos MQTT al ESP32.
