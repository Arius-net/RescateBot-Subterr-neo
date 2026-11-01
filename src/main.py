"""
Orquestador principal y capa de persistencia transaccional para RescateBot Subterráneo.

Este módulo integra el flujo completo del backend:
1. Verifica/inicializa la base de datos SQLite embebida (`rescate.db`).
2. Instancia `EntornoCaverna` cargando el mapa estático y la configuración en memoria RAM.
3. Inicializa `BuscadorRutaOptima` y ejecuta la búsqueda A* desde `N1_ENTRADA` hasta
   `N6_CAMARA_RESCATE` iniciando en modo `NORMAL`.
4. Persiste atómicamente los resultados de la misión en las tablas `misiones` (cabecera)
   e `historial_rutas` (detalle paso a paso) mediante `guardar_mision_db`.
"""

from pathlib import Path
import sqlite3
from typing import Optional, Tuple, Union

try:
    from src.a_star_solver import BuscadorRutaOptima, ResultadoBusquedaAStar
    from src.db_setup import RUTA_DB_DEFECTO, inicializar_base_datos
    from src.rescate_bot import EntornoCaverna, MODO_NORMAL
except ImportError:
    from a_star_solver import BuscadorRutaOptima, ResultadoBusquedaAStar
    from db_setup import RUTA_DB_DEFECTO, inicializar_base_datos
    from rescate_bot import EntornoCaverna, MODO_NORMAL


CODIGO_NODO_ORIGEN = "N1_ENTRADA"
CODIGO_NODO_META = "N6_CAMARA_RESCATE"


def resolver_id_nodo_por_codigo(entorno: EntornoCaverna, codigo_nodo: str) -> int:
    """
    Busca en la memoria RAM del `EntornoCaverna` el identificador numérico (`id_nodo`)
    correspondiente a un código simbólico de cámara (ej. 'N1_ENTRADA').

    Args:
        entorno: Instancia de `EntornoCaverna` con los nodos cargados.
        codigo_nodo: Nombre único del nodo buscado.

    Returns:
        Identificador entero `id_nodo`.

    Raises:
        KeyError: Si el `codigo_nodo` no existe en el mapa cargado.
    """
    for id_nodo, nodo in entorno.nodos.items():
        if nodo.codigo_nodo == codigo_nodo:
            return id_nodo
    raise KeyError(f"No se encontró ningún nodo con codigo_nodo='{codigo_nodo}'.")


def guardar_mision_db(
    resultado: ResultadoBusquedaAStar,
    db_path: Union[str, Path] = RUTA_DB_DEFECTO,
    id_config: int = 1,
) -> int:
    """
    Persiste de manera transaccional el resultado global de la búsqueda A* en la tabla
    `misiones` y la secuencia detallada paso a paso en la tabla `historial_rutas`.

    Ejecuta un `COMMIT` explícito al finalizar ambas inserciones para garantizar
    atomicidad (ACID) en el archivo SQLite local.

    Args:
        resultado: Objeto `ResultadoBusquedaAStar` retornado por `BuscadorRutaOptima.buscar_ruta`.
        db_path: Ruta al archivo de base de datos SQLite (`rescate.db`).
        id_config: Identificador del perfil en `configuracion_robot` empleado en la misión.

    Returns:
        Identificador primario `id_mision` generado en la tabla `misiones`.
    """
    ruta_sqlite = Path(db_path)
    estado_mision = "COMPLETADA" if resultado.encontrada else "SIN_RUTA"

    with sqlite3.connect(str(ruta_sqlite)) as conexion:
        cursor = conexion.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        # 1. INSERT en tabla de cabecera `misiones`
        cursor.execute(
            """
            INSERT INTO misiones (
                id_config,
                nodo_origen_id,
                nodo_meta_id,
                modo_inicial,
                modo_final,
                bateria_total_consumida,
                distancia_total_metros,
                total_transformaciones,
                estado_mision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                id_config,
                resultado.nodo_origen_id,
                resultado.nodo_meta_id,
                resultado.modo_inicial,
                resultado.modo_final,
                resultado.bateria_total_consumida,
                resultado.distancia_total_metros,
                resultado.total_transformaciones,
                estado_mision,
            ),
        )

        id_mision = int(cursor.lastrowid)

        # 2. Múltiples INSERT en tabla de detalle `historial_rutas`
        if resultado.pasos_ruta:
            registros_detalle = [
                (
                    id_mision,
                    paso.orden_paso,
                    paso.estado.id_nodo,
                    paso.estado.modo,
                    paso.tipo_accion,
                    paso.costo_paso_bateria,
                    paso.bateria_acumulada,
                )
                for paso in resultado.pasos_ruta
            ]

            cursor.executemany(
                """
                INSERT INTO historial_rutas (
                    id_mision,
                    orden_paso,
                    id_nodo,
                    modo_utilizado,
                    tipo_accion,
                    costo_paso_bateria,
                    bateria_acumulada
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                registros_detalle,
            )

        # Confirmación explícita de la transacción
        conexion.commit()

    return id_mision


def mostrar_reporte_consola(
    resultado: ResultadoBusquedaAStar,
    entorno: EntornoCaverna,
    id_mision: int,
) -> None:
    """
    Muestra en salida estándar el desglose detallado de la ruta óptima calculada
    y confirmada en la base de datos.

    Args:
        resultado: Resultado de la búsqueda A*.
        entorno: Entorno de caverna en memoria para resolver nombres de nodos.
        id_mision: Identificador de misión registrado en SQLite.
    """
    nodo_origen = entorno.obtener_nodo(resultado.nodo_origen_id)
    nodo_meta = entorno.obtener_nodo(resultado.nodo_meta_id)

    print("=" * 78)
    print("  RESCATEBOT SUBTERRÁNEO — REPORTE DE PLANIFICACIÓN ÓPTIMA (A*)")
    print("=" * 78)
    print(f"ID Misión Persistida (SQLite) : {id_mision}")
    print(f"Nodo Origen                   : [{nodo_origen.id_nodo}] {nodo_origen.codigo_nodo}")
    print(f"Nodo Meta                     : [{nodo_meta.id_nodo}] {nodo_meta.codigo_nodo}")
    print(f"Modo Inicial / Modo Final     : {resultado.modo_inicial} -> {resultado.modo_final}")
    print(f"Estado de la Misión           : {'COMPLETADA' if resultado.encontrada else 'SIN_RUTA'}")
    print(f"Batería Total Consumida g*(n) : {resultado.bateria_total_consumida:.2f} u.b.")
    print(f"Distancia Física Recorrida    : {resultado.distancia_total_metros:.2f} m")
    print(f"Total de Transformaciones     : {resultado.total_transformaciones}")
    print(f"Estados Expandidos en RAM     : {resultado.estados_expandidos}")
    print("-" * 78)
    print(
        f"{'Paso':<6} | {'Nodo':<22} | {'Modo':<9} | {'Acción':<16} | "
        f"{'Costo Paso':>10} | {'Acumulado':>10}"
    )
    print("-" * 78)

    for paso in resultado.pasos_ruta:
        info_nodo = entorno.obtener_nodo(paso.estado.id_nodo)
        etiqueta_nodo = f"[{info_nodo.id_nodo}] {info_nodo.codigo_nodo}"
        print(
            f"{paso.orden_paso:<6} | {etiqueta_nodo:<22} | {paso.estado.modo:<9} | "
            f"{paso.tipo_accion:<16} | {paso.costo_paso_bateria:>10.2f} | "
            f"{paso.bateria_acumulada:>10.2f}"
        )
    print("=" * 78)


def ejecutar_mision_rescate(
    ruta_db: Optional[Union[str, Path]] = None,
) -> Tuple[int, ResultadoBusquedaAStar]:
    """
    Función principal de orquestación del backend.

    Garantiza que la base de datos exista, carga el entorno en RAM, ejecuta el
    solucionador A* desde `N1_ENTRADA` hasta `N6_CAMARA_RESCATE` comenzando en
    modo `NORMAL`, y persiste la misión resultante en SQLite.

    Args:
        ruta_db: Ruta opcional al archivo `rescate.db`.

    Returns:
        Tupla `(id_mision, resultado_busqueda)`.
    """
    db_path = Path(ruta_db) if ruta_db is not None else RUTA_DB_DEFECTO

    # Si el archivo SQLite aún no ha sido creado, inicializarlo y sembrarlo
    if not db_path.exists():
        inicializar_base_datos(db_path)

    # 1. Instanciar EntornoCaverna y volcar mapa estático en memoria RAM
    entorno = EntornoCaverna(ruta_db=db_path)

    # 2. Inicializar el motor de búsqueda óptima A*
    buscador = BuscadorRutaOptima(entorno=entorno)

    # 3. Resolver identificadores de N1_ENTRADA y N6_CAMARA_RESCATE y ejecutar A*
    nodo_origen_id = resolver_id_nodo_por_codigo(entorno, CODIGO_NODO_ORIGEN)
    nodo_meta_id = resolver_id_nodo_por_codigo(entorno, CODIGO_NODO_META)

    resultado = buscador.buscar_ruta(
        nodo_origen_id=nodo_origen_id,
        nodo_meta_id=nodo_meta_id,
        modo_inicial=MODO_NORMAL,
    )

    # 4. Persistir cabecera y detalle de la ruta óptima en SQLite
    id_config = entorno.configuracion.id_config if entorno.configuracion else 1
    id_mision = guardar_mision_db(
        resultado=resultado,
        db_path=db_path,
        id_config=id_config,
    )

    mostrar_reporte_consola(resultado=resultado, entorno=entorno, id_mision=id_mision)
    return id_mision, resultado


if __name__ == "__main__":
    ejecutar_mision_rescate()
