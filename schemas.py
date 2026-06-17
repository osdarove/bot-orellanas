"""
Esquemas Pydantic usados para serializar las respuestas de la API.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class TelemetriaOut(BaseModel):
    id: int
    timestamp: datetime
    etapa: Optional[str] = None
    temp1: Optional[float] = None
    hum1: Optional[float] = None
    temp2: Optional[float] = None
    hum2: Optional[float] = None
    temp_promedio: Optional[float] = None
    hum_promedio: Optional[float] = None
    hum_control: Optional[float] = None
    bomba: Optional[str] = None
    ventilador: Optional[str] = None
    modo: Optional[str] = None
    tanque: Optional[str] = None
    minutos_bomba_hoy: Optional[float] = None

    class Config:
        from_attributes = True


class AlertaOut(BaseModel):
    id: int
    timestamp: datetime
    evento: Optional[str] = None
    mensaje: Optional[str] = None

    class Config:
        from_attributes = True


class ComandoIn(BaseModel):
    comando: str
