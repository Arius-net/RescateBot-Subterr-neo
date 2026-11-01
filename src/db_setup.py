"""
Módulo de inicialización y sembrado (seed) de la base de datos SQLite (rescate.db).

Este script define el esquema relacional completo (DDL) del proyecto RescateBot Subterráneo
e inserta la configuración energética del robot junto con el mapa estático de prueba (6 nodos
y 7 aristas bidireccionales canónicas) diseñado para evaluar el dilema de optimización entre
túneles anchos de rodeo y grietas estrechas directas.
"""

from pathlib import Path
import sqlite3
from typing import Optional, Union


RUTA_DB_DEFECTO = Path(__file__).resolve().parent.parent / "rescate.db"

DDL_ESQUEMA_SQLITE = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS nodos (
    id_nodo INTEGER PRIMARY KEY,
    codigo_nodo TEXT NOT NULL UNIQUE,
    coord_x REAL NOT NULL,
    coord_y REAL NOT NULL,
    descripcion TEXT
);

CREATE TABLE IF NOT EXISTS aristas (
    id_arista INTEGER PRIMARY KEY AUTOINCREMENT,
    nodo_origen_id INTEGER NOT NULL,
    nodo_destino_id INTEGER NOT NULL,
    longitud_metros REAL NOT NULL CHECK (longitud_metros > 0.0),
    tipo_tunel TEXT NOT NULL CHECK (tipo_tunel IN ('ANCHO', 'ESTRECHO')),
    CONSTRAINT chk_orden_canonico CHECK (nodo_origen_id < nodo_destino_id),
    CONSTRAINT uq_arista_no_dirigida UNIQUE (nodo_origen_id, nodo_destino_id),
    FOREIGN KEY (nodo_origen_id) REFERENCES nodos(id_nodo) ON DELETE RESTRICT,
    FOREIGN KEY (nodo_destino_id) REFERENCES nodos(id_nodo) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS configuracion_robot (
    id_config INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_perfil TEXT NOT NULL UNIQUE,
    factor_consumo_normal REAL NOT NULL CHECK (factor_consumo_normal > 0.0),
    factor_consumo_plegado REAL NOT NULL CHECK (factor_consumo_plegado > factor_consumo_normal),
    costo_transformacion REAL NOT NULL CHECK (costo_transformacion >= 0.0),
    modo_inicial_defecto TEXT NOT NULL DEFAULT 'NORMAL' CHECK (modo_inicial_defecto IN ('NORMAL', 'PLEGADO'))
);

CREATE TABLE IF NOT EXISTS misiones (
    id_mision INTEGER PRIMARY KEY AUTOINCREMENT,
    id_config INTEGER NOT NULL,
    nodo_origen_id INTEGER NOT NULL,
    nodo_meta_id INTEGER NOT NULL,
    modo_inicial TEXT NOT NULL DEFAULT 'NORMAL' CHECK (modo_inicial IN ('NORMAL', 'PLEGADO')),
    modo_final TEXT CHECK (modo_final IN ('NORMAL', 'PLEGADO')),
    bateria_total_consumida REAL NOT NULL DEFAULT 0.0 CHECK (bateria_total_consumida >= 0.0),
    distancia_total_metros REAL NOT NULL DEFAULT 0.0 CHECK (distancia_total_metros >= 0.0),
    total_transformaciones INTEGER NOT NULL DEFAULT 0 CHECK (total_transformaciones >= 0),
    estado_mision TEXT NOT NULL DEFAULT 'COMPLETADA' CHECK (estado_mision IN ('COMPLETADA', 'SIN_RUTA')),
    fecha_ejecucion TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (id_config) REFERENCES configuracion_robot(id_config) ON DELETE RESTRICT,
    FOREIGN KEY (nodo_origen_id) REFERENCES nodos(id_nodo) ON DELETE RESTRICT,
    FOREIGN KEY (nodo_meta_id) REFERENCES nodos(id_nodo) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS historial_rutas (
    id_historial INTEGER PRIMARY KEY AUTOINCREMENT,
    id_mision INTEGER NOT NULL,
    orden_paso INTEGER NOT NULL CHECK (orden_paso >= 0),
    id_nodo INTEGER NOT NULL,
    modo_utilizado TEXT NOT NULL CHECK (modo_utilizado IN ('NORMAL', 'PLEGADO')),
    tipo_accion TEXT NOT NULL CHECK (tipo_accion IN ('INICIO', 'DESPLAZAMIENTO', 'TRANSFORMACION')),
    costo_paso_bateria REAL NOT NULL DEFAULT 0.0 CHECK (costo_paso_bateria >= 0.0),
    bateria_acumulada REAL NOT NULL DEFAULT 0.0 CHECK (bateria_acumulada >= 0.0),
    CONSTRAINT uq_mision_paso UNIQUE (id_mision, orden_paso),
    FOREIGN KEY (id_mision) REFERENCES misiones(id_mision) ON DELETE CASCADE,
    FOREIGN KEY (id_nodo) REFERENCES nodos(id_nodo) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_aristas_origen ON aristas(nodo_origen_id);
CREATE INDEX IF NOT EXISTS idx_aristas_destino ON aristas(nodo_destino_id);
CREATE INDEX IF NOT EXISTS idx_historial_mision_orden ON historial_rutas(id_mision, orden_paso);
"""


def crear_esquema(conexion: sqlite3.Connection) -> None:
    """
    Ejecuta las sentencias DDL para crear las tablas e índices del sistema.

    Args:
        conexion: Conexión activa a la base de datos SQLite.
    """
    conexion.executescript(DDL_ESQUEMA_SQLITE)


def sembrar_datos_iniciales(conexion: sqlite3.Connection) -> None:
    """
    Inserta de forma idempotente la configuración paramétrica del robot y el
    mapa estático de prueba (6 nodos y 7 aristas bidireccionales canónicas).

    Diseño del Mapa de Prueba:
    - Origen: Nodo 1 (N1_ENTRADA) en (0, 0).
    - Meta: Nodo 6 (N6_CAMARA_RESCATE) en (46, 0).
    - Camino A (Directo con Grieta Estrecha: 1 -> 2 -> 5 -> 6):
        Distancias: (1-2)=10m ANCHO, (2-5)=20m ESTRECHO, (5-6)=16m ANCHO.
        * Variante A1 (Plegarse en N2 y seguir PLEGADO hasta N6):
          10*1.0 + 15 (transf) + 20*2.5 + 16*2.5 = 10 + 15 + 50 + 40 = 115.0 u.b.
        * Variante A2 (Plegarse en N2, cruzar 2->5, desplegarse a NORMAL en N5, cruzar 5->6):
          10*1.0 + 15 (transf) + 20*2.5 + 15 (transf) + 16*1.0 = 106.0 u.b. [ÓPTIMO GLOBAL]
    - Camino B (Rodeo 100% ANCHO: 1 -> 2 -> 3 -> 4 -> 6):
        Distancias: (1-2)=10m, (2-3)=35m, (3-4)=36m, (4-6)=35m (todos ANCHOS).
        * Sin transformaciones (siempre en modo NORMAL):
          (10 + 35 + 36 + 35) * 1.0 = 116.0 u.b.

    Args:
        conexion: Conexión activa a la base de datos SQLite.
    """
    cursor = conexion.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")

    # Limpieza ordenada de datos previos para garantizar sembrado determinista
    cursor.execute("DELETE FROM historial_rutas;")
    cursor.execute("DELETE FROM misiones;")
    cursor.execute("DELETE FROM aristas;")
    cursor.execute("DELETE FROM nodos;")
    cursor.execute("DELETE FROM configuracion_robot;")

    # 1. Sembrado de Configuración Energética del Robot
    cursor.execute(
        """
        INSERT INTO configuracion_robot (
            id_config,
            nombre_perfil,
            factor_consumo_normal,
            factor_consumo_plegado,
            costo_transformacion,
            modo_inicial_defecto
        ) VALUES (?, ?, ?, ?, ?, ?);
        """,
        (1, "RESCATEBOT_ESTANDAR_V1", 1.0, 2.5, 15.0, "NORMAL"),
    )

    # 2. Sembrado de Nodos Estáticos (Coordenadas 2D en metros)
    nodos_prueba = [
        (1, "N1_ENTRADA", 0.0, 0.0, "Acceso principal a la caverna (Origen)"),
        (2, "N2_BIFURCACION", 10.0, 0.0, "Cámara de decisión: inicio de grieta sur o galería norte"),
        (3, "N3_GALERIA_NORTE", 10.0, 30.0, "Conducto amplio superior oeste (Rodeo Camino B)"),
        (4, "N4_BOVEDA_ESTE", 46.0, 30.0, "Bóveda amplia superior este (Rodeo Camino B)"),
        (5, "N5_CONFLUENCIA_SUR", 30.0, 0.0, "Salida de la grieta estrecha principal (Camino A)"),
        (6, "N6_CAMARA_RESCATE", 46.0, 0.0, "Zona de supervivencia objetivo (Meta de rescate)"),
    ]

    cursor.executemany(
        """
        INSERT INTO nodos (id_nodo, codigo_nodo, coord_x, coord_y, descripcion)
        VALUES (?, ?, ?, ?, ?);
        """,
        nodos_prueba,
    )

    # 3. Sembrado de Aristas Bidireccionales Canónicas (nodo_origen_id < nodo_destino_id)
    # Todas las longitudes cumplen estrictamente: longitud_metros >= distancia_euclidiana(u, v)
    aristas_prueba = [
        # Tramo inicial común (d_euclid = 10.0m)
        (1, 2, 10.0, "ANCHO"),
        # Camino A: Atajo por grieta estrecha directa (d_euclid = 20.0m)
        (2, 5, 20.0, "ESTRECHO"),
        # Camino A: Tramo final ancho hacia la meta (d_euclid = 16.0m)
        (5, 6, 16.0, "ANCHO"),
        # Camino B: Rodeo ancho ascenso norte (d_euclid = 30.0m)
        (2, 3, 35.0, "ANCHO"),
        # Camino B: Rodeo ancho galería superior (d_euclid = 36.0m)
        (3, 4, 36.0, "ANCHO"),
        # Camino B: Rodeo ancho descenso hacia la meta (d_euclid = 30.0m)
        (4, 6, 35.0, "ANCHO"),
        # Conexión diagonal secundaria estrecha entre bóveda este y confluencia sur (d_euclid = 34.0m)
        (4, 5, 34.0, "ESTRECHO"),
    ]

    cursor.executemany(
        """
        INSERT INTO aristas (nodo_origen_id, nodo_destino_id, longitud_metros, tipo_tunel)
        VALUES (?, ?, ?, ?);
        """,
        aristas_prueba,
    )

    conexion.commit()


def inicializar_base_datos(ruta_db: Optional[Union[str, Path]] = None) -> Path:
    """
    Orquesta la creación del archivo SQLite, la ejecución del DDL y el sembrado
    del entorno estático de prueba.

    Args:
        ruta_db: Ruta opcional al archivo .db. Si es None, usa RUTA_DB_DEFECTO.

    Returns:
        Objeto Path apuntando al archivo de base de datos inicializado.
    """
    ruta_final = Path(ruta_db) if ruta_db is not None else RUTA_DB_DEFECTO

    with sqlite3.connect(str(ruta_final)) as conexion:
        crear_esquema(conexion)
        sembrar_datos_iniciales(conexion)

    return ruta_final


if __name__ == "__main__":
    ruta_creada = inicializar_base_datos()
    print(f"Base de datos inicializada y sembrada exitosamente en: {ruta_creada}")

