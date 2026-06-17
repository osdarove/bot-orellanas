"""
Backend FastAPI para el sistema de automatización de orellanas.

Responsabilidades:
  1. Conectarse al broker MQTT (mismo broker que usa el ESP32) y
     guardar cada telemetría y alerta en PostgreSQL (vía mqtt_client.py).
  2. Exponer una API REST para:
       - Ver el último estado del sistema.
       - Consultar historial de telemetría y alertas.
       - Enviar comandos al ESP32 (bomba_on, vent_off, etc.).
  3. Servir un dashboard HTML simple para visualizar todo sin
     necesidad de un cliente MQTT aparte.
"""

from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import Base, engine, get_db
import models
import schemas
from mqtt_client import iniciar_cliente_mqtt, publicar_comando, obtener_ultimo_estado

# Comandos válidos que el ESP32 entiende (definidos en main.py)
COMANDOS_VALIDOS = {
    "auto", "bomba_on", "bomba_off",
    "vent_on", "vent_off", "vent_auto",
    "incubacion", "primordios", "fructificacion",
    "estado",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---------- Arranque de la aplicación ----------
    # Crea las tablas en la base de datos si no existen todavía.
    Base.metadata.create_all(bind=engine)
    # Inicia el cliente MQTT en un hilo de fondo.
    iniciar_cliente_mqtt()
    yield
    # ---------- Apagado de la aplicación (nada que limpiar) ----------


app = FastAPI(
    title="Orellanas Backend",
    description="API y puente MQTT-PostgreSQL para el cultivo automatizado de orellanas.",
    version="1.0.0",
    lifespan=lifespan,
)

# Habilita CORS para poder consumir la API desde cualquier frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================================
# ENDPOINTS DE ESTADO
# =====================================================================
@app.get("/")
def raiz():
    """Endpoint raíz simple para verificar que el servicio está vivo."""
    return {"status": "ok", "servicio": "orellanas-backend"}


@app.get("/api/estado")
def estado_actual():
    """
    Retorna el último JSON de telemetría recibido por MQTT,
    guardado en memoria (no requiere ir a la base de datos).
    """
    estado = obtener_ultimo_estado()
    if not estado:
        return {"mensaje": "Aún no se ha recibido telemetría del ESP32."}
    return estado


# =====================================================================
# ENDPOINTS DE HISTORIAL (PostgreSQL)
# =====================================================================
@app.get("/api/telemetria", response_model=List[schemas.TelemetriaOut])
def historial_telemetria(limite: int = 100, db: Session = Depends(get_db)):
    """
    Retorna las últimas N filas de telemetría guardadas en la base
    de datos, ordenadas de la más reciente a la más antigua.
    """
    limite = max(1, min(limite, 1000))  # protegemos contra valores absurdos
    filas = (
        db.query(models.Telemetria)
        .order_by(desc(models.Telemetria.id))
        .limit(limite)
        .all()
    )
    return filas


@app.get("/api/alertas", response_model=List[schemas.AlertaOut])
def historial_alertas(
    limite: int = 100,
    evento: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Retorna las últimas N alertas guardadas. Permite filtrar
    opcionalmente por tipo de evento (?evento=tanque_vacio).
    """
    limite = max(1, min(limite, 1000))
    query = db.query(models.Alerta)
    if evento:
        query = query.filter(models.Alerta.evento == evento)
    filas = query.order_by(desc(models.Alerta.id)).limit(limite).all()
    return filas


# =====================================================================
# ENDPOINT DE COMANDOS (envía al ESP32 vía MQTT)
# =====================================================================
@app.post("/api/comando")
def enviar_comando(payload: schemas.ComandoIn):
    """
    Publica un comando en el tópico invernadero/orellanas/cmd para
    que el ESP32 lo ejecute. Valida que el comando sea uno de los
    soportados por main.py antes de publicarlo.
    """
    comando = payload.comando.strip().lower()

    if comando not in COMANDOS_VALIDOS:
        raise HTTPException(
            status_code=400,
            detail="Comando inválido. Válidos: {}".format(sorted(COMANDOS_VALIDOS)),
        )

    publicado = publicar_comando(comando)
    if not publicado:
        raise HTTPException(
            status_code=503,
            detail="No se pudo publicar el comando: cliente MQTT no disponible.",
        )

    return {"status": "ok", "comando_enviado": comando}


# =====================================================================
# DASHBOARD WEB SIMPLE (HTML + JS, sin frameworks)
# =====================================================================
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """
    Página HTML simple que consulta /api/estado cada 5 segundos y
    permite enviar comandos al ESP32 con botones, sin necesidad de
    instalar nada aparte.
    """
    return """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Orellanas - Panel de Control</title>
<style>
  body { font-family: Arial, sans-serif; background:#f4f6f5; margin:0; padding:20px; }
  h1 { color:#2e4d2e; }
  .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr)); gap:12px; margin-bottom:24px; }
  .card { background:white; border-radius:10px; padding:16px; box-shadow:0 1px 4px rgba(0,0,0,0.1); }
  .card h3 { margin:0 0 6px; font-size:13px; color:#888; text-transform:uppercase; }
  .card p { margin:0; font-size:22px; font-weight:bold; color:#222; }
  .botones { display:flex; flex-wrap:wrap; gap:8px; }
  button { padding:10px 16px; border:none; border-radius:6px; background:#3a7d44; color:white; cursor:pointer; font-size:14px; }
  button:hover { background:#2e6336; }
  .ok { color:#2e7d32; } .alerta { color:#c62828; }
</style>
</head>
<body>
  <h1>🍄 Panel de Control - Cultivo de Orellanas</h1>

  <div class="grid" id="grid"></div>

  <h3>Comandos</h3>
  <div class="botones">
    <button onclick="enviar('auto')">Auto (todo)</button>
    <button onclick="enviar('bomba_on')">Bomba ON</button>
    <button onclick="enviar('bomba_off')">Bomba OFF</button>
    <button onclick="enviar('vent_on')">Vent ON</button>
    <button onclick="enviar('vent_off')">Vent OFF</button>
    <button onclick="enviar('vent_auto')">Vent AUTO</button>
    <button onclick="enviar('incubacion')">Etapa: Incubación</button>
    <button onclick="enviar('primordios')">Etapa: Primordios</button>
    <button onclick="enviar('fructificacion')">Etapa: Fructificación</button>
    <button onclick="enviar('estado')">Forzar estado</button>
  </div>

  <p id="msg" style="margin-top:12px;"></p>

<script>
async function refrescar() {
  try {
    const res = await fetch('/api/estado');
    const data = await res.json();
    const grid = document.getElementById('grid');
    if (data.mensaje) {
      grid.innerHTML = '<div class="card"><p>' + data.mensaje + '</p></div>';
      return;
    }
    grid.innerHTML = `
      <div class="card"><h3>Etapa</h3><p>${data.etapa}</p></div>
      <div class="card"><h3>Temp. Promedio</h3><p>${data.temp_promedio} °C</p></div>
      <div class="card"><h3>Hum. Promedio</h3><p>${data.hum_promedio} %</p></div>
      <div class="card"><h3>Hum. Control</h3><p>${data.hum_control} %</p></div>
      <div class="card"><h3>Bomba</h3><p class="${data.bomba==='ON'?'ok':''}">${data.bomba}</p></div>
      <div class="card"><h3>Ventilador</h3><p class="${data.ventilador==='ON'?'ok':''}">${data.ventilador}</p></div>
      <div class="card"><h3>Modo</h3><p>${data.modo}</p></div>
      <div class="card"><h3>Tanque</h3><p class="${data.tanque==='VACIO'?'alerta':'ok'}">${data.tanque}</p></div>
      <div class="card"><h3>Min. bomba hoy</h3><p>${data.minutos_bomba_hoy}</p></div>
    `;
  } catch (e) {
    console.error(e);
  }
}

async function enviar(comando) {
  const msg = document.getElementById('msg');
  msg.textContent = 'Enviando ' + comando + '...';
  try {
    const res = await fetch('/api/comando', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({comando})
    });
    const data = await res.json();
    msg.textContent = res.ok ? ('Comando enviado: ' + comando) : ('Error: ' + data.detail);
  } catch (e) {
    msg.textContent = 'Error de red al enviar comando.';
  }
}

refrescar();
setInterval(refrescar, 5000);
</script>
</body>
</html>
"""
