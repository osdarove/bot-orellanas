"""
Configuración de la conexión a la base de datos PostgreSQL.

Railway inyecta automáticamente la variable de entorno DATABASE_URL
cuando agregas el plugin de PostgreSQL al proyecto. Si esa variable
no existe (por ejemplo, corriendo localmente), se usa SQLite como
respaldo para poder probar sin Postgres instalado.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Railway entrega la URL como "postgres://...", pero SQLAlchemy con
# el driver psycopg necesita el prefijo "postgresql://". Lo corregimos
# automáticamente si es necesario.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./orellanas.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# connect_args solo es necesario para SQLite (modo de prueba local)
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """
    Dependencia de FastAPI: entrega una sesión de base de datos
    y se asegura de cerrarla al finalizar la petición.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
