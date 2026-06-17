"""
Modelos de base de datos (SQLAlchemy) para el backend de orellanas.

Tablas:
  - telemetria: cada lectura periódica que envía el ESP32
  - alerta: cada evento de alarma publicado por el ESP32
"""

from sqlalchemy import Column, Integer, Float, String, DateTime
from sqlalchemy.sql import func
from .database import Base


class Telemetria(Base):
    """
    Una fila por cada mensaje de telemetría recibido vía MQTT
    en el tópico invernadero/orellanas.
    """
    __tablename__ = "telemetria"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    etapa = Column(String, nullable=True)
    temp1 = Column(Float, nullable=True)
    hum1 = Column(Float, nullable=True)
    temp2 = Column(Float, nullable=True)
    hum2 = Column(Float, nullable=True)
    temp_promedio = Column(Float, nullable=True)
    hum_promedio = Column(Float, nullable=True)
    hum_control = Column(Float, nullable=True)
    bomba = Column(String, nullable=True)
    ventilador = Column(String, nullable=True)
    modo = Column(String, nullable=True)
    tanque = Column(String, nullable=True)
    minutos_bomba_hoy = Column(Float, nullable=True)


class Alerta(Base):
    """
    Una fila por cada alarma recibida vía MQTT
    en el tópico invernadero/orellanas/alertas.
    """
    __tablename__ = "alerta"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    evento = Column(String, index=True, nullable=True)
    mensaje = Column(String, nullable=True)
    payload_crudo = Column(String, nullable=True)
