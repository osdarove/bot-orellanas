"""
=====================================================================
  AUTOMATIZACION CULTIVO DE ORELLANAS (Pleurotus ostreatus)
  ESP32 + MicroPython - Compatible con Wokwi
=====================================================================

  Sensores:
    GPIO15 -> DHT22 #1
    GPIO4  -> DHT22 #2
  Actuadores:
    GPIO18 -> Rele bomba (activo en HIGH)
    GPIO21 -> Rele ventilador (activo en HIGH)
  Entradas:
    GPIO19 -> Flotador de nivel de agua (NO -> 0 = con agua, 1 = vacio)

  Broker MQTT: broker.hivemq.com
  Cliente MQTT: orellanas

  Topicos:
    invernadero/orellanas          -> telemetria (cada 10s)
    invernadero/orellanas/cmd      -> comandos entrantes
    invernadero/orellanas/alertas  -> alarmas

=====================================================================
"""

import network
import time
import ujson
import dht
from machine import Pin, reset
from umqtt.simple import MQTTClient

# =====================================================================
# CONFIGURACION GENERAL WIFI
# =====================================================================
WIFI_SSID = "FAMILIA_VENEGAS"
WIFI_PASSWORD = "20677289"

# =====================================================================
# CONFIGURACION MQTT
# =====================================================================
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_CLIENT_ID = "orellanas"

TOPICO_TELEMETRIA = b"invernadero/orellanas"
TOPICO_COMANDOS = b"invernadero/orellanas/cmd"
TOPICO_ALERTAS = b"invernadero/orellanas/alertas"

# =====================================================================
# CONFIGURACION DE PINES
# =====================================================================
PIN_DHT1 = 15
PIN_DHT2 = 4
PIN_BOMBA = 18
PIN_VENTILADOR = 21
PIN_FLOTADOR = 19

sensor_dht1 = dht.DHT22(Pin(PIN_DHT1))
sensor_dht2 = dht.DHT22(Pin(PIN_DHT2))

rele_bomba = Pin(PIN_BOMBA, Pin.OUT)
rele_ventilador = Pin(PIN_VENTILADOR, Pin.OUT)
flotador = Pin(PIN_FLOTADOR, Pin.IN)

# Aseguramos que todo arranque apagado (activo en HIGH)
rele_bomba.value(0)
rele_ventilador.value(0)

# =====================================================================
# VARIABLES DE ETAPA DE CULTIVO
# =====================================================================
# Etapa actual del cultivo. Puede ser: "incubacion", "primordios",
# "fructificacion"
ETAPA = "fructificacion"

# Estos valores se recalculan cada vez que cambia la etapa mediante
# configurar_etapa()
HUMEDAD_MIN = 0
HUMEDAD_MAX = 0


def configurar_etapa():
    """
    Configura los umbrales de humedad (HUMEDAD_MIN / HUMEDAD_MAX)
    segun la etapa actual de cultivo almacenada en la variable ETAPA.
    """
    global HUMEDAD_MIN, HUMEDAD_MAX

    if ETAPA == "incubacion":
        HUMEDAD_MIN = 75
        HUMEDAD_MAX = 82
    elif ETAPA == "primordios":
        HUMEDAD_MIN = 92
        HUMEDAD_MAX = 96
    elif ETAPA == "fructificacion":
        HUMEDAD_MIN = 88
        HUMEDAD_MAX = 94
    else:
        # Etapa desconocida -> usamos valores seguros por defecto
        HUMEDAD_MIN = 80
        HUMEDAD_MAX = 90

    print("[ETAPA] Configurada etapa '{}' -> MIN={} MAX={}".format(
        ETAPA, HUMEDAD_MIN, HUMEDAD_MAX))


# Configuramos los umbrales segun la etapa inicial
configurar_etapa()

# =====================================================================
# VARIABLES DE CONTROL DE BOMBA
# =====================================================================
TIEMPO_MIN_BOMBA = 30    # segundos minimos que debe permanecer encendida
TIEMPO_MAX_BOMBA = 300   # segundos maximos continuos de funcionamiento

bomba_estado = False          # Estado actual de la bomba (True = ON)
bomba_tiempo_inicio = 0       # Marca de tiempo (ticks) cuando se encendio
tiempo_total_bomba = 0        # Acumulado de segundos de funcionamiento (hoy)
tanque_vacio = False           # Bandera de bloqueo por tanque vacio

# Marca del ultimo ciclo para acumular tiempo de bomba encendida
_ultimo_tick_acumulado = time.time()

# =====================================================================
# VARIABLES DE CONTROL DE VENTILADOR
# =====================================================================
VENT_ON_SEG = 120     # 2 minutos encendido
VENT_OFF_SEG = 1080   # 18 minutos apagado

TEMP_VENT_ON = 28     # Temperatura que fuerza encendido del ventilador
TEMP_VENT_OFF = 26    # Temperatura bajo la cual se libera el forzado

ventilador_estado = False        # Estado actual del ventilador
vent_ciclo_inicio = time.time()  # Marca de tiempo del inicio del ciclo actual
vent_forzado_temp = False        # True si el ventilador esta forzado por temperatura

# =====================================================================
# MODOS DE OPERACION
# =====================================================================
# modo_bomba: "auto" o "manual"
modo_bomba = "auto"

# modo_ventilador: "auto", "manual_on", "manual_off"
modo_ventilador = "auto"

# =====================================================================
# HISTORIAL PARA PROMEDIO MOVIL DE HUMEDAD
# =====================================================================
historial_humedad = []  # Guarda las ultimas 3 muestras de hum_promedio

# =====================================================================
# CONTROL DE ALARMAS (anti-spam: 1 misma alarma cada 30 min max)
# =====================================================================
INTERVALO_ALARMA = 1800  # 30 minutos en segundos
ultimas_alarmas = {}      # diccionario {evento: timestamp_ultimo_envio}

# =====================================================================
# VARIABLES GLOBALES DE SENSORES / TELEMETRIA
# =====================================================================
temp1 = 0.0
hum1 = 0.0
temp2 = 0.0
hum2 = 0.0
temp_promedio = 0.0
hum_promedio = 0.0
hum_control = 0.0
sensores_ok = True

# Timers de tareas periodicas
ultimo_envio_telemetria = time.time()
INTERVALO_TELEMETRIA = 10  # segundos

ultima_lectura_sensores = time.time()
INTERVALO_LECTURA_SENSORES = 2  # segundos

# Cliente MQTT global
cliente_mqtt = None

# Control de reconexion WiFi
ultimo_intento_wifi = 0
INTERVALO_REINTENTO_WIFI = 5  # segundos


# =====================================================================
# FUNCION: CONECTAR WIFI
# =====================================================================
def conectar_wifi():
    """
    Conecta el ESP32 a la red WiFi configurada (Wokwi-GUEST, sin clave).
    Esta funcion bloquea hasta lograr conexion o agotar los intentos,
    y se puede volver a llamar para reconectar.
    """
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        return wlan

    print("[WIFI] Conectando a {}...".format(WIFI_SSID))
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    intentos = 0
    while not wlan.isconnected() and intentos < 20:
        time.sleep(0.5)
        intentos += 1
        print("[WIFI] Esperando conexion...")

    if wlan.isconnected():
        print("[WIFI] Conectado. IP:", wlan.ifconfig()[0])
    else:
        print("[WIFI] No se pudo conectar.")

    return wlan


def wifi_conectado():
    """
    Retorna True si el ESP32 tiene conexion WiFi activa.
    """
    wlan = network.WLAN(network.STA_IF)
    return wlan.isconnected()


def verificar_reconexion_wifi():
    """
    Verifica periodicamente si el WiFi sigue conectado.
    Si se perdio la conexion, intenta reconectar sin bloquear
    demasiado tiempo el loop principal.
    """
    global ultimo_intento_wifi

    if wifi_conectado():
        return True

    ahora = time.time()
    if ahora - ultimo_intento_wifi >= INTERVALO_REINTENTO_WIFI:
        ultimo_intento_wifi = ahora
        print("[WIFI] Conexion perdida. Reintentando...")
        conectar_wifi()

    return wifi_conectado()


# =====================================================================
# FUNCION: CALLBACK MQTT (RECEPCION DE COMANDOS)
# =====================================================================
def mqtt_callback(topic, msg):
    """
    Callback que se ejecuta cuando llega un mensaje MQTT en algun
    topico suscrito. Interpreta el comando recibido y actua en
    consecuencia.
    """
    global modo_bomba, modo_ventilador, ETAPA, vent_forzado_temp

    try:
        comando = msg.decode("utf-8").strip().lower()
    except Exception as e:
        print("[MQTT] Error decodificando mensaje:", e)
        return

    print("[MQTT] Comando recibido:", comando)

    # ----------------- MODO AUTOMATICO GENERAL -----------------
    if comando == "auto":
        modo_bomba = "auto"
        modo_ventilador = "auto"
        vent_forzado_temp = False
        publicar_evento("modo_auto", "Modo automatico activado (bomba y ventilador).")
        print("[CMD] Modo automatico activado (bomba y ventilador).")

    # ----------------- CONTROL MANUAL DE BOMBA -----------------
    elif comando == "bomba_on":
        modo_bomba = "manual"
        encender_bomba(forzado_manual=True)
        print("[CMD] Bomba encendida manualmente.")

    elif comando == "bomba_off":
        modo_bomba = "manual"
        apagar_bomba(forzado_manual=True)
        print("[CMD] Bomba apagada manualmente.")

    # ----------------- CONTROL MANUAL DE VENTILADOR -----------------
    elif comando == "vent_on":
        modo_ventilador = "manual_on"
        encender_ventilador()
        print("[CMD] Ventilador encendido manualmente.")

    elif comando == "vent_off":
        modo_ventilador = "manual_off"
        apagar_ventilador()
        print("[CMD] Ventilador apagado manualmente.")

    elif comando == "vent_auto":
        modo_ventilador = "auto"
        vent_forzado_temp = False
        publicar_evento("vent_auto", "Ventilador vuelve a modo automatico.")
        print("[CMD] Ventilador vuelve a modo automatico.")

    # ----------------- CAMBIO DE ETAPA DE CULTIVO -----------------
    elif comando in ("incubacion", "primordios", "fructificacion"):
        if ETAPA != comando:
            etapa_anterior = ETAPA
            ETAPA = comando
            configurar_etapa()
            publicar_alarma(
                "cambio_etapa",
                "Etapa cambiada de {} a {}".format(etapa_anterior, ETAPA)
            )
            publicar_evento("cambio_etapa", "Etapa cambiada de {} a {}".format(etapa_anterior, ETAPA))
        print("[CMD] Etapa de cultivo establecida en:", ETAPA)

    # ----------------- SOLICITUD DE ESTADO INMEDIATO -----------------
    elif comando == "estado":
        publicar_telemetria(forzado=True)
        print("[CMD] Estado publicado por solicitud.")

    else:
        print("[CMD] Comando no reconocido:", comando)


# =====================================================================
# FUNCION: CONECTAR MQTT
# =====================================================================
def conectar_mqtt():
    """
    Crea el cliente MQTT, define el callback y se conecta al broker.
    Se suscribe al topico de comandos.
    Retorna el objeto cliente conectado.
    """
    global cliente_mqtt

    cliente_mqtt = MQTTClient(
        MQTT_CLIENT_ID,
        MQTT_BROKER,
        port=MQTT_PORT,
        keepalive=60
    )
    cliente_mqtt.set_callback(mqtt_callback)

    intentos = 0
    conectado = False
    while not conectado and intentos < 10:
        try:
            cliente_mqtt.connect()
            conectado = True
        except Exception as e:
            intentos += 1
            print("[MQTT] Error al conectar ({}). Reintentando...".format(e))
            time.sleep(2)

    if conectado:
        cliente_mqtt.subscribe(TOPICO_COMANDOS)
        print("[MQTT] Conectado al broker y suscrito a comandos.")
    else:
        print("[MQTT] No se pudo conectar al broker tras varios intentos.")

    return cliente_mqtt


def verificar_mqtt():
    """
    Verifica si hay mensajes MQTT pendientes (no bloqueante) y
    reconecta el cliente MQTT en caso de error de comunicacion.
    """
    global cliente_mqtt

    try:
        cliente_mqtt.check_msg()
    except Exception as e:
        print("[MQTT] Error de comunicacion ({}). Reconectando...".format(e))
        try:
            conectar_mqtt()
        except Exception as e2:
            print("[MQTT] Fallo al reconectar:", e2)


# =====================================================================
# FUNCION: PUBLICAR ALARMA (CON ANTI-SPAM)
# =====================================================================
def publicar_evento(evento, mensaje=""):
    """
    Publica un evento de accion en el topico de alertas MQTT.
    Estos mensajes no tienen el filtro anti-spam de alarmas.
    """
    payload = {
        "evento": evento,
        "mensaje": mensaje,
        "timestamp": time.time()
    }

    try:
        cliente_mqtt.publish(TOPICO_ALERTAS, ujson.dumps(payload))
        print("[EVENTO] Publicado:", payload)
    except Exception as e:
        print("[EVENTO] Error publicando evento ({}). Reconectando MQTT...".format(e))
        try:
            conectar_mqtt()
            cliente_mqtt.publish(TOPICO_ALERTAS, ujson.dumps(payload))
        except Exception as e2:
            print("[EVENTO] Fallo definitivo al publicar:", e2)


def publicar_alarma(evento, mensaje=""):
    """
    Publica una alarma en el topico de alertas MQTT, evitando reenviar
    el mismo tipo de evento mas de una vez cada INTERVALO_ALARMA
    segundos (30 minutos por defecto).

    evento: identificador corto del tipo de alarma
            (bomba_on, bomba_off, tanque_vacio, temperatura_alta,
             tiempo_maximo_bomba, sensor_error, cambio_etapa)
    mensaje: texto descriptivo adicional (opcional)
    """
    global ultimas_alarmas

    ahora = time.time()
    ultimo_envio = ultimas_alarmas.get(evento, 0)

    # Si ya se envio este mismo evento hace menos del intervalo, se ignora
    if (ahora - ultimo_envio) < INTERVALO_ALARMA:
        print("[ALARMA] '{}' suprimida (anti-spam).".format(evento))
        return

    ultimas_alarmas[evento] = ahora

    payload = {
        "evento": evento,
        "mensaje": mensaje,
        "timestamp": ahora
    }

    try:
        cliente_mqtt.publish(TOPICO_ALERTAS, ujson.dumps(payload))
        print("[ALARMA] Publicada:", payload)
    except Exception as e:
        print("[ALARMA] Error publicando alarma ({}). Reconectando MQTT...".format(e))
        try:
            conectar_mqtt()
            cliente_mqtt.publish(TOPICO_ALERTAS, ujson.dumps(payload))
        except Exception as e2:
            print("[ALARMA] Fallo definitivo al publicar:", e2)


# =====================================================================
# FUNCION: LEER SENSORES DHT22
# =====================================================================
def leer_sensores():
    """
    Lee los dos sensores DHT22 y calcula los promedios de temperatura
    y humedad. Actualiza las variables globales correspondientes.
    En caso de error de lectura, publica una alarma 'sensor_error'
    y conserva los ultimos valores validos.
    """
    global temp1, hum1, temp2, hum2, temp_promedio, hum_promedio
    global sensores_ok

    error_detectado = False

    # ---------- Lectura del sensor 1 ----------
    try:
        sensor_dht1.measure()
        temp1 = sensor_dht1.temperature()
        hum1 = sensor_dht1.humidity()
    except Exception as e:
        print("[DHT1] Error de lectura:", e)
        error_detectado = True

    # ---------- Lectura del sensor 2 ----------
    try:
        sensor_dht2.measure()
        temp2 = sensor_dht2.temperature()
        hum2 = sensor_dht2.humidity()
    except Exception as e:
        print("[DHT2] Error de lectura:", e)
        error_detectado = True

    if error_detectado:
        sensores_ok = False
        publicar_alarma("sensor_error", "Fallo en lectura de uno o ambos DHT22")
    else:
        sensores_ok = True

    # ---------- Calculo de promedios entre los dos sensores ----------
    temp_promedio = (temp1 + temp2) / 2
    hum_promedio = (hum1 + hum2) / 2

    # ---------- Actualizacion del promedio movil de humedad ----------
    actualizar_promedio_movil(hum_promedio)


def actualizar_promedio_movil(valor_hum):
    """
    Mantiene un historial con las ultimas 3 muestras de humedad
    promedio y calcula hum_control como el promedio de dichas
    muestras (promedio movil simple).
    """
    global historial_humedad, hum_control

    historial_humedad.append(valor_hum)
    if len(historial_humedad) > 3:
        historial_humedad.pop(0)

    hum_control = sum(historial_humedad) / len(historial_humedad)


# =====================================================================
# FUNCION: LEER FLOTADOR / NIVEL DE AGUA
# =====================================================================
def verificar_nivel_agua():
    """
    Lee el estado del flotador (NO):
      GPIO19 = 0 -> Tanque con agua
      GPIO19 = 1 -> Tanque vacio

    Si el tanque esta vacio:
      - Apaga inmediatamente la bomba.
      - Bloquea el encendido de la bomba (bandera tanque_vacio).
      - Genera una alarma MQTT.
    """
    global tanque_vacio

    nivel = flotador.value()

    if nivel == 1:
        # Tanque vacio detectado
        if not tanque_vacio:
            # Primera vez que se detecta -> apagamos bomba y alertamos
            apagar_bomba(forzado_manual=True)
            publicar_alarma("tanque_vacio", "Nivel de agua bajo. Bomba bloqueada.")
        tanque_vacio = True
    else:
        if tanque_vacio:
            publicar_evento("tanque_ok", "Nivel de agua normal. Bloqueo de bomba liberado.")
        tanque_vacio = False


# =====================================================================
# FUNCIONES DE CONTROL DE BOMBA
# =====================================================================
def encender_bomba(forzado_manual=False):
    """
    Enciende la bomba de riego, respetando el bloqueo por tanque vacio.
    Registra el instante de encendido para aplicar los tiempos
    minimo/maximo de funcionamiento.
    """
    global bomba_estado, bomba_tiempo_inicio

    # No se permite encender la bomba si el tanque esta vacio
    if tanque_vacio:
        print("[BOMBA] Bloqueada: tanque vacio.")
        publicar_alarma("tanque_vacio", "Intento de encender bomba con tanque vacio.")
        return

    if not bomba_estado:
        rele_bomba.value(1)
        bomba_estado = True
        bomba_tiempo_inicio = time.time()
        publicar_alarma("bomba_on", "Bomba encendida.")
        publicar_evento("bomba_on", "Bomba encendida.")
        print("[BOMBA] Encendida.")


def apagar_bomba(forzado_manual=False):
    """
    Apaga la bomba de riego.
    Si no es un apagado forzado (manual o por seguridad), se respeta
    el tiempo minimo de funcionamiento (TIEMPO_MIN_BOMBA) antes de
    permitir el apagado por control automatico.
    """
    global bomba_estado

    if not bomba_estado:
        return  # Ya esta apagada, nada que hacer

    tiempo_encendida = time.time() - bomba_tiempo_inicio

    # Si el apagado NO es forzado (es decision del control automatico),
    # se respeta el tiempo minimo de encendido.
    if not forzado_manual and tiempo_encendida < TIEMPO_MIN_BOMBA:
        print("[BOMBA] Aun no cumple tiempo minimo ({}s). No se apaga.".format(
            int(tiempo_encendida)))
        return

    rele_bomba.value(0)
    bomba_estado = False
    publicar_alarma("bomba_off", "Bomba apagada.")
    publicar_evento("bomba_off", "Bomba apagada.")
    print("[BOMBA] Apagada. Tiempo encendida: {}s".format(int(tiempo_encendida)))


def actualizar_contador_bomba():
    """
    Acumula el tiempo total de funcionamiento de la bomba
    (tiempo_total_bomba) sumando los segundos transcurridos desde
    la ultima actualizacion, solo si la bomba esta encendida.

    Tambien controla el tiempo maximo de funcionamiento continuo:
    si se supera TIEMPO_MAX_BOMBA, apaga la bomba y genera alarma.
    """
    global tiempo_total_bomba, _ultimo_tick_acumulado

    ahora = time.time()
    delta = ahora - _ultimo_tick_acumulado
    _ultimo_tick_acumulado = ahora

    if bomba_estado:
        tiempo_total_bomba += delta

        tiempo_encendida = ahora - bomba_tiempo_inicio
        if tiempo_encendida > TIEMPO_MAX_BOMBA:
            print("[BOMBA] Tiempo maximo superado. Apagando por seguridad.")
            apagar_bomba(forzado_manual=True)
            publicar_alarma(
                "tiempo_maximo_bomba",
                "La bomba supero el tiempo maximo continuo de {}s".format(TIEMPO_MAX_BOMBA)
            )


def control_automatico_bomba():
    """
    Logica de control automatico de la bomba en funcion del promedio
    movil de humedad (hum_control) y de los umbrales de la etapa
    actual (HUMEDAD_MIN / HUMEDAD_MAX).

    Solo actua si el sistema esta en modo_bomba == "auto" y el
    tanque no esta vacio.
    """
    if modo_bomba != "auto":
        return

    if tanque_vacio:
        # Nunca se enciende con tanque vacio
        return

    if hum_control < HUMEDAD_MIN:
        encender_bomba()
    elif hum_control > HUMEDAD_MAX:
        apagar_bomba()  # Respeta tiempo minimo internamente


# =====================================================================
# FUNCIONES DE CONTROL DE VENTILADOR
# =====================================================================
def encender_ventilador():
    """
    Enciende el rele del ventilador (activo en HIGH).
    """
    global ventilador_estado
    if not ventilador_estado:
        rele_ventilador.value(1)
        ventilador_estado = True
        publicar_evento("vent_on", "Ventilador encendido.")


def apagar_ventilador():
    """
    Apaga el rele del ventilador (activo en HIGH).
    """
    global ventilador_estado
    if ventilador_estado:
        rele_ventilador.value(0)
        ventilador_estado = False
        publicar_evento("vent_off", "Ventilador apagado.")


def control_automatico_ventilador():
    """
    Control automatico del ventilador combinando dos estrategias:

    1) Ciclo temporizado:
       - VENT_ON_SEG (2 min) encendido
       - VENT_OFF_SEG (18 min) apagado

    2) Control por temperatura (tiene prioridad sobre el ciclo):
       - Si temp_promedio >= TEMP_VENT_ON -> fuerza encendido continuo.
       - Si temp_promedio <= TEMP_VENT_OFF -> libera el forzado y
         vuelve a dejar que el ciclo temporizado controle el ventilador.

    Solo aplica si modo_ventilador == "auto". Los modos manual_on y
    manual_off son gestionados directamente por el callback MQTT.
    """
    global vent_ciclo_inicio, vent_forzado_temp

    if modo_ventilador != "auto":
        return

    # ---------- Control prioritario por temperatura alta ----------
    if temp_promedio >= TEMP_VENT_ON:
        if not vent_forzado_temp:
            vent_forzado_temp = True
            publicar_alarma(
                "temperatura_alta",
                "Temperatura promedio {}C >= {}C. Ventilador forzado ON.".format(
                    temp_promedio, TEMP_VENT_ON)
            )
            publicar_evento(
                "vent_forzado_on",
                "Temperatura alta. Ventilador forzado ON." 
            )
        encender_ventilador()
        return  # El forzado por temperatura tiene prioridad total

    # ---------- Liberacion del forzado cuando baja la temperatura ----------
    if vent_forzado_temp and temp_promedio <= TEMP_VENT_OFF:
        vent_forzado_temp = False
        # Reiniciamos el ciclo temporizado desde cero al liberar el forzado
        vent_ciclo_inicio = time.time()
        print("[VENTILADOR] Temperatura normalizada. Vuelve a ciclo automatico.")

    # ---------- Si sigue forzado (entre TEMP_VENT_OFF y TEMP_VENT_ON) ----------
    if vent_forzado_temp:
        encender_ventilador()
        return

    # ---------- Ciclo temporizado normal (ON/OFF) ----------
    ahora = time.time()
    tiempo_en_ciclo = ahora - vent_ciclo_inicio

    if ventilador_estado:
        # Esta encendido: revisamos si ya cumplio el tiempo ON
        if tiempo_en_ciclo >= VENT_ON_SEG:
            apagar_ventilador()
            vent_ciclo_inicio = ahora
    else:
        # Esta apagado: revisamos si ya cumplio el tiempo OFF
        if tiempo_en_ciclo >= VENT_OFF_SEG:
            encender_ventilador()
            vent_ciclo_inicio = ahora


# =====================================================================
# FUNCION: PUBLICAR TELEMETRIA
# =====================================================================
def publicar_telemetria(forzado=False):
    """
    Construye y publica el JSON de telemetria en el topico
    invernadero/orellanas. Se llama automaticamente cada
    INTERVALO_TELEMETRIA segundos, o de forma inmediata si
    forzado=True (por ejemplo, ante el comando 'estado').
    """
    global ultimo_envio_telemetria

    ahora = time.time()
    if not forzado and (ahora - ultimo_envio_telemetria) < INTERVALO_TELEMETRIA:
        return

    ultimo_envio_telemetria = ahora

    minutos_bomba_hoy = round(tiempo_total_bomba / 60, 2)

    payload = {
        "etapa": ETAPA,
        "temp1": round(temp1, 1),
        "hum1": round(hum1, 1),
        "temp2": round(temp2, 1),
        "hum2": round(hum2, 1),
        "temp_promedio": round(temp_promedio, 1),
        "hum_promedio": round(hum_promedio, 1),
        "hum_control": round(hum_control, 1),
        "bomba": "ON" if bomba_estado else "OFF",
        "ventilador": "ON" if ventilador_estado else "OFF",
        "modo": modo_bomba,
        "tanque": "VACIO" if tanque_vacio else "OK",
        "minutos_bomba_hoy": minutos_bomba_hoy
    }

    try:
        cliente_mqtt.publish(TOPICO_TELEMETRIA, ujson.dumps(payload))
        print("[TELEMETRIA]", payload)
    except Exception as e:
        print("[TELEMETRIA] Error al publicar ({}). Reconectando MQTT...".format(e))
        try:
            conectar_mqtt()
            cliente_mqtt.publish(TOPICO_TELEMETRIA, ujson.dumps(payload))
        except Exception as e2:
            print("[TELEMETRIA] Fallo definitivo al publicar:", e2)


# =====================================================================
# PROGRAMA PRINCIPAL
# =====================================================================
def main():
    """
    Funcion principal: inicializa WiFi y MQTT, y ejecuta el bucle
    infinito de control del sistema (lectura de sensores, control de
    bomba y ventilador, publicacion de telemetria y atencion de
    comandos MQTT).
    """
    print("=== Iniciando sistema de automatizacion de orellanas ===")

    # ---------- Inicializacion de WiFi ----------
    conectar_wifi()

    # ---------- Inicializacion de MQTT ----------
    conectar_mqtt()

    # Pequena espera inicial para estabilizar sensores
    time.sleep(2)

    while True:
        try:
            # ---------- Mantenimiento de conexiones ----------
            verificar_reconexion_wifi()
            verificar_mqtt()

            ahora = time.time()

            # ---------- Lectura periodica de sensores ----------
            global ultima_lectura_sensores
            if (ahora - ultima_lectura_sensores) >= INTERVALO_LECTURA_SENSORES:
                ultima_lectura_sensores = ahora
                leer_sensores()

            # ---------- Verificacion de nivel de agua (flotador) ----------
            verificar_nivel_agua()

            # ---------- Control automatico de bomba ----------
            control_automatico_bomba()

            # ---------- Actualizacion de contador y tiempo maximo de bomba ----------
            actualizar_contador_bomba()

            # ---------- Control automatico de ventilador ----------
            control_automatico_ventilador()

            # ---------- Publicacion periodica de telemetria ----------
            publicar_telemetria()

            # Pequena pausa para no saturar el CPU / loop
            time.sleep(0.5)

        except Exception as e:
            # Captura general de errores para que el sistema nunca se
            # detenga por completo; se reporta el error y se continua.
            print("[MAIN] Error inesperado en el bucle principal:", e)
            time.sleep(1)


# =====================================================================
# PUNTO DE ENTRADA
# =====================================================================
if __name__ == "__main__":
    main()