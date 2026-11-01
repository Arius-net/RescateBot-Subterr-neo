"""
Módulo de estructuras base del dominio y carga en memoria para RescateBot Subterráneo.

Contiene las clases fundamentales para representar el estado cinemático-topológico
del robot (`EstadoRobot`) y la representación en memoria RAM del sistema de cavernas
(`EntornoCaverna`), eliminando cuellos de botella de I/O contra SQLite durante la
futura ejecución del algoritmo A*.
"""

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Dict, List, Optional, Tuple, Union


MODO_NORMAL = "NORMAL"
MODO_PLEGADO = "PLEGADO"
MODOS_VALIDOS = frozenset({MODO_NORMAL, MODO_PLEGADO})

TUNEL_ANCHO = "ANCHO"
TUNEL_ESTRECHO = "ESTRECHO"
TIPOS_TUNEL_VALIDOS = frozenset({TUNEL_ANCHO, TUNEL_ESTRECHO})

RUTA_DB_DEFECTO = Path(__file__).resolve().parent.parent / "rescate.db"


@dataclass(frozen=True, order=True)
class EstadoRobot:
    """
    Representa un estado único dentro del espacio de búsqueda aumentado (nodo, modo).

    Al estar decorada con `@dataclass(frozen=True, order=True)`, las instancias son
    inmutables, comparables y hashables (`__hash__` y `__eq__` generados automáticamente).
    Esto permite utilizar `EstadoRobot` directamente como clave en diccionarios de
    costos acumulados `g(n)`, conjuntos de visitados y tuplas de colas de prioridad (`heapq`).

    Attributes:
        id_nodo: Identificador entero del nodo (cámara subterránea) actual.
        modo: Modo físico actual del chasis ('NORMAL' o 'PLEGADO').
    """

    id_nodo: int
    modo: str = MODO_NORMAL

    def __post_init__(self) -> None:
        """Valida la integridad de dominio del estado al instanciarse."""
        if not isinstance(self.id_nodo, int) or self.id_nodo <= 0:
            raise ValueError(f"El id_nodo debe ser un entero positivo, recibido: {self.id_nodo}")
        if self.modo not in MODOS_VALIDOS:
            raise ValueError(
                f"Modo físico inválido '{self.modo}'. Valores permitidos: {sorted(MODOS_VALIDOS)}"
            )

    def modo_opuesto(self) -> str:
        """
        Devuelve el modo físico alternativo al actual para operaciones de transformación.

        Returns:
            'PLEGADO' si el modo actual es 'NORMAL', o 'NORMAL' en caso contrario.
        """
        return MODO_PLEGADO if self.modo == MODO_NORMAL else MODO_NORMAL

    def transformar(self) -> "EstadoRobot":
        """
        Genera un nuevo `EstadoRobot` en el mismo nodo pero con el chasis transformado.

        Returns:
            Nueva instancia inmutable de `EstadoRobot` con el modo conmutado.
        """
        return EstadoRobot(id_nodo=self.id_nodo, modo=self.modo_opuesto())


@dataclass(frozen=True)
class NodoCaverna:
    """
    Estructura inmutable en memoria que almacena los atributos topológicos de un nodo.

    Attributes:
        id_nodo: Identificador único del nodo en la base de datos.
        codigo_nodo: Nombre o etiqueta única de la cámara.
        coord_x: Coordenada espacial X en metros (plano 2D).
        coord_y: Coordenada espacial Y en metros (plano 2D).
        descripcion: Descripción textual de la cámara subterránea.
    """

    id_nodo: int
    codigo_nodo: str
    coord_x: float
    coord_y: float
    descripcion: str = ""


@dataclass(frozen=True)
class ConexionTunel:
    """
    Representa una transición de adyacencia dirigida en memoria hacia un nodo vecino.

    Attributes:
        id_arista: Identificador original de la arista en SQLite.
        nodo_vecino_id: Identificador del nodo destino alcanzable.
        longitud_metros: Longitud física fija del pasaje en metros.
        tipo_tunel: Clasificación geométrica ('ANCHO' o 'ESTRECHO').
    """

    id_arista: int
    nodo_vecino_id: int
    longitud_metros: float
    tipo_tunel: str


@dataclass(frozen=True)
class ConfiguracionEnergetica:
    """
    Parámetros físicos y tasas de consumo de batería del RescateBot cargados en RAM.

    Attributes:
        id_config: Identificador primario del perfil en base de datos.
        nombre_perfil: Identificador textual del perfil energético.
        factor_consumo_normal: Unidades de batería por metro en modo 'NORMAL'.
        factor_consumo_plegado: Unidades de batería por metro en modo 'PLEGADO'.
        costo_transformacion: Costo fijo de batería por conmutar entre modos.
        modo_inicial_defecto: Modo de inicio por defecto ('NORMAL' o 'PLEGADO').
    """

    id_config: int
    nombre_perfil: str
    factor_consumo_normal: float
    factor_consumo_plegado: float
    costo_transformacion: float
    modo_inicial_defecto: str


class EntornoCaverna:
    """
    Gestor del entorno estático subterráneo con almacenamiento íntegro en memoria RAM.

    Se conecta una única vez al archivo SQLite (`rescate.db`) durante su inicialización
    (o mediante `cargar_desde_sqlite`), vuelca los nodos, las aristas bidireccionales y
    la configuración energética en diccionarios y listas de adyacencia en memoria, y
    cierra inmediatamente la conexión de lectura para garantizar cero consumo de I/O
    en disco durante la ejecución del algoritmo de búsqueda.
    """

    def __init__(
        self,
        ruta_db: Optional[Union[str, Path]] = None,
        nombre_perfil: str = "RESCATEBOT_ESTANDAR_V1",
        cargar_automaticamente: bool = True,
    ) -> None:
        """
        Inicializa el contenedor del entorno de cavernas.

        Args:
            ruta_db: Ruta al archivo `rescate.db`. Si es None, usa RUTA_DB_DEFECTO.
            nombre_perfil: Perfil energético a cargar desde `configuracion_robot`.
            cargar_automaticamente: Si es True, ejecuta la carga desde SQLite al instanciar.
        """
        self.ruta_db: Path = Path(ruta_db) if ruta_db is not None else RUTA_DB_DEFECTO
        self.nombre_perfil: str = nombre_perfil

        # Estructuras en memoria RAM para acceso O(1)
        self.nodos: Dict[int, NodoCaverna] = {}
        self.adyacencia: Dict[int, List[ConexionTunel]] = {}
        self.configuracion: Optional[ConfiguracionEnergetica] = None

        if cargar_automaticamente:
            self.cargar_desde_sqlite()

    def cargar_desde_sqlite(self) -> None:
        """
        Extrae el mapa estático completo y la configuración del robot desde SQLite
        hacia las estructuras en memoria RAM, expandiendo cada arista canónica (u < v)
        en sus dos direcciones simétricas (u -> v y v -> u).

        Raises:
            FileNotFoundError: Si el archivo SQLite especificado no existe.
            ValueError: Si no se encuentra el perfil de configuración solicitado.
        """
        if not self.ruta_db.exists():
            raise FileNotFoundError(
                f"No se encontró la base de datos SQLite en: {self.ruta_db}"
            )

        self.nodos.clear()
        self.adyacencia.clear()

        with sqlite3.connect(str(self.ruta_db)) as conexion:
            conexion.row_factory = sqlite3.Row
            cursor = conexion.cursor()

            # 1. Cargar configuración energética del robot
            cursor.execute(
                """
                SELECT
                    id_config,
                    nombre_perfil,
                    factor_consumo_normal,
                    factor_consumo_plegado,
                    costo_transformacion,
                    modo_inicial_defecto
                FROM configuracion_robot
                WHERE nombre_perfil = ?;
                """,
                (self.nombre_perfil,),
            )
            fila_config = cursor.fetchone()
            if fila_config is None:
                raise ValueError(
                    f"No existe el perfil '{self.nombre_perfil}' en la tabla configuracion_robot."
                )

            self.configuracion = ConfiguracionEnergetica(
                id_config= int(fila_config["id_config"]),
                nombre_perfil=str(fila_config["nombre_perfil"]),
                factor_consumo_normal=float(fila_config["factor_consumo_normal"]),
                factor_consumo_plegado=float(fila_config["factor_consumo_plegado"]),
                costo_transformacion=float(fila_config["costo_transformacion"]),
                modo_inicial_defecto=str(fila_config["modo_inicial_defecto"]),
            )

            # 2. Cargar todos los nodos estáticos en diccionario O(1)
            cursor.execute(
                """
                SELECT id_nodo, codigo_nodo, coord_x, coord_y, descripcion
                FROM nodos
                ORDER BY id_nodo ASC;
                """
            )
            for fila_nodo in cursor.fetchall():
                id_nodo = int(fila_nodo["id_nodo"])
                self.nodos[id_nodo] = NodoCaverna(
                    id_nodo=id_nodo,
                    codigo_nodo=str(fila_nodo["codigo_nodo"]),
                    coord_x=float(fila_nodo["coord_x"]),
                    coord_y=float(fila_nodo["coord_y"]),
                    descripcion=str(fila_nodo["descripcion"] or ""),
                )
                self.adyacencia[id_nodo] = []

            # 3. Cargar aristas y expandir bidireccionalmente en la lista de adyacencia
            cursor.execute(
                """
                SELECT id_arista, nodo_origen_id, nodo_destino_id, longitud_metros, tipo_tunel
                FROM aristas
                ORDER BY id_arista ASC;
                """
            )
            for fila_arista in cursor.fetchall():
                id_arista = int(fila_arista["id_arista"])
                u = int(fila_arista["nodo_origen_id"])
                v = int(fila_arista["nodo_destino_id"])
                longitud = float(fila_arista["longitud_metros"])
                tipo_tunel = str(fila_arista["tipo_tunel"])

                # Dirección u -> v
                self.adyacencia[u].append(
                    ConexionTunel(
                        id_arista=id_arista,
                        nodo_vecino_id=v,
                        longitud_metros=longitud,
                        tipo_tunel=tipo_tunel,
                    )
                )
                # Dirección simétrica v -> u (Grafo no dirigido)
                self.adyacencia[v].append(
                    ConexionTunel(
                        id_arista=id_arista,
                        nodo_vecino_id=u,
                        longitud_metros=longitud,
                        tipo_tunel=tipo_tunel,
                    )
                )

    def obtener_nodo(self, id_nodo: int) -> NodoCaverna:
        """
        Recupera desde la RAM los metadatos y coordenadas de un nodo en tiempo O(1).

        Args:
            id_nodo: Identificador del nodo a consultar.

        Returns:
            Instancia `NodoCaverna` correspondiente.

        Raises:
            KeyError: Si el `id_nodo` no existe en el mapa cargado.
        """
        if id_nodo not in self.nodos:
            raise KeyError(f"El nodo con id_nodo={id_nodo} no existe en el entorno cargado.")
        return self.nodos[id_nodo]

    def obtener_coordenadas(self, id_nodo: int) -> Tuple[float, float]:
        """
        Devuelve el par de coordenadas espaciales 2D (x, y) de un nodo desde la RAM.

        Args:
            id_nodo: Identificador del nodo.

        Returns:
            Tupla `(coord_x, coord_y)` en metros.
        """
        nodo = self.obtener_nodo(id_nodo)
        return (nodo.coord_x, nodo.coord_y)

    def obtener_vecinos(self, id_nodo: int) -> List[ConexionTunel]:
        """
        Devuelve la lista de túneles adyacentes que parten desde `id_nodo` en tiempo O(1).

        Args:
            id_nodo: Identificador del nodo origen.

        Returns:
            Lista de objetos `ConexionTunel` conectados a `id_nodo`.
        """
        if id_nodo not in self.adyacencia:
            raise KeyError(f"El nodo con id_nodo={id_nodo} no existe en la lista de adyacencia.")
        return self.adyacencia[id_nodo]

    @staticmethod
    def es_tunel_transitable(tipo_tunel: str, modo_actual: str) -> bool:
        """
        Evalúa la regla de negocio física que determina si el robot puede cruzar un túnel
        dado su modo cinemático actual.

        Regla:
        - Túnel 'ANCHO': Transitable en modo 'NORMAL' y en modo 'PLEGADO'.
        - Grieta 'ESTRECHO': Transitable únicamente en modo 'PLEGADO'.

        Args:
            tipo_tunel: Tipo geométrico de la arista ('ANCHO' o 'ESTRECHO').
            modo_actual: Configuración física activa del robot ('NORMAL' o 'PLEGADO').

        Returns:
            True si el pasaje es físicamente transitable, False en caso contrario.
        """
        if tipo_tunel == TUNEL_ANCHO:
            return modo_actual in MODOS_VALIDOS
        if tipo_tunel == TUNEL_ESTRECHO:
            return modo_actual == MODO_PLEGADO
        return False

