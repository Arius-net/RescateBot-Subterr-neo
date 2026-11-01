"""
Motor de búsqueda heurística óptima A* para RescateBot Subterráneo.

Este módulo implementa la clase `BuscadorRutaOptima`, la cual resuelve el problema de
minimización de consumo de batería en un grafo de espacio de estados aumentado (nodo, modo),
evaluando de forma óptima el compromiso entre rodear por túneles anchos en modo NORMAL o
invertir energía en transformar el chasis a modo PLEGADO para atravesar grietas estrechas.
"""

from dataclasses import dataclass
import heapq
import math
from typing import Dict, List, Optional, Tuple

try:
    from src.rescate_bot import (
        ConfiguracionEnergetica,
        EntornoCaverna,
        EstadoRobot,
        MODO_NORMAL,
        MODO_PLEGADO,
    )
except ImportError:
    from rescate_bot import (
        ConfiguracionEnergetica,
        EntornoCaverna,
        EstadoRobot,
        MODO_NORMAL,
        MODO_PLEGADO,
    )


ACCION_INICIO = "INICIO"
ACCION_DESPLAZAMIENTO = "DESPLAZAMIENTO"
ACCION_TRANSFORMACION = "TRANSFORMACION"


@dataclass(frozen=True)
class TransicionEstado:
    """
    Representa una transición válida generada por la función sucesora desde un estado origen.

    Attributes:
        estado_siguiente: Estado destino (id_nodo, modo) tras aplicar la acción.
        costo_paso: Consumo marginal de batería (\Delta g) de esta transición.
        tipo_accion: 'DESPLAZAMIENTO' (cambio de nodo) o 'TRANSFORMACION' (cambio de modo).
        longitud_metros: Distancia física recorrida en metros (0.0 si es transformación).
    """

    estado_siguiente: EstadoRobot
    costo_paso: float
    tipo_accion: str
    longitud_metros: float = 0.0


@dataclass(frozen=True)
class PasoRutaOptima:
    """
    Representa un paso individual dentro de la secuencia ordenada de la ruta óptima reconstruida.

    Establece correspondencia directa 1 a 1 con los atributos de la tabla transaccional
    `historial_rutas` de la base de datos SQLite.

    Attributes:
        orden_paso: Índice secuencial del paso (0 para el estado inicial, 1..k para transiciones).
        estado: Instancia `EstadoRobot` con el `id_nodo` y el `modo` en este punto.
        tipo_accion: 'INICIO', 'DESPLAZAMIENTO' o 'TRANSFORMACION'.
        costo_paso_bateria: Batería marginal consumida para llegar a este paso.
        bateria_acumulada: Costo acumulado g(n) desde el origen hasta este paso.
        distancia_paso_metros: Metros recorridos en este paso (0.0 en 'INICIO' y 'TRANSFORMACION').
    """

    orden_paso: int
    estado: EstadoRobot
    tipo_accion: str
    costo_paso_bateria: float
    bateria_acumulada: float
    distancia_paso_metros: float = 0.0


@dataclass(frozen=True)
class ResultadoBusquedaAStar:
    """
    Objeto de transferencia que encapsula el resultado global y el detalle paso a paso
    de una ejecución del algoritmo A*.

    Attributes:
        encontrada: True si se halló una ruta válida hasta `nodo_meta_id`, False en caso contrario.
        nodo_origen_id: Identificador del nodo de partida.
        nodo_meta_id: Identificador del nodo objetivo de rescate.
        modo_inicial: Modo físico inicial del robot.
        modo_final: Modo físico con el que el robot alcanzó la meta (None si no hay ruta).
        bateria_total_consumida: Costo óptimo acumulado final g*(meta).
        distancia_total_metros: Suma total de metros físicos recorridos en la ruta óptima.
        total_transformaciones: Cantidad de cambios de chasis ejecutados en la ruta óptima.
        estados_expandidos: Total de estados extraídos y expandidos desde la cola de prioridad.
        pasos_ruta: Lista ordenada de objetos `PasoRutaOptima` desde el paso 0 hasta la meta.
    """

    encontrada: bool
    nodo_origen_id: int
    nodo_meta_id: int
    modo_inicial: str
    modo_final: Optional[str]
    bateria_total_consumida: float
    distancia_total_metros: float
    total_transformaciones: int
    estados_expandidos: int
    pasos_ruta: List[PasoRutaOptima]


class BuscadorRutaOptima:
    """
    Motor de búsqueda heurística óptima A* sobre el espacio de estados (id_nodo, modo).

    Opera exclusivamente sobre las estructuras en memoria RAM provistas por `EntornoCaverna`,
    garantizando cero operaciones de entrada/salida en disco durante la planificación.
    """

    def __init__(self, entorno: EntornoCaverna) -> None:
        """
        Inicializa el solucionador A* vinculándolo a un entorno de caverna cargado en RAM.

        Args:
            entorno: Instancia de `EntornoCaverna` con nodos, adyacencias y configuración.

        Raises:
            ValueError: Si el entorno proporcionado no tiene cargada la configuración energética.
        """
        if entorno.configuracion is None:
            raise ValueError(
                "El EntornoCaverna debe tener una configuración energética cargada en RAM."
            )
        self.entorno: EntornoCaverna = entorno
        self.configuracion: ConfiguracionEnergetica = entorno.configuracion

    def calcular_heuristica(self, estado_actual: EstadoRobot, nodo_meta_id: int) -> float:
        """
        Calcula el valor heurístico h(n) desde `estado_actual` hasta `nodo_meta_id`.

        Fórmula aplicada:
            h(u, m) = Distancia_Euclidiana_2D(u, meta) * factor_consumo_normal

        -------------------------------------------------------------------------
        JUSTIFICACIÓN DE ADMISIBILIDAD (h(n) <= h*(n)) FRENTE A TÚNELES ESTRECHOS:
        -------------------------------------------------------------------------
        1. Cota Inferior Geométrica: En el espacio euclidiano 2D, la línea recta
           entre las coordenadas (x_u, y_u) del nodo actual y (x_m, y_m) de la meta
           es la distancia física mínima posible. Toda ruta real por túneles cumple:
               Distancia_Ruta_Real >= Distancia_Euclidiana_2D(u, meta).

        2. Cota Inferior Energética frente a Túneles Estrechos: Por restricción de
           dominio en la base de datos (factor_consumo_plegado > factor_consumo_normal > 0),
           el modo 'NORMAL' posee el costo energético por metro más bajo de todo el
           sistema. Si la ruta óptima real se ve obligada a cruzar un túnel 'ESTRECHO',
           el robot deberá transitarlo obligatoriamente en modo 'PLEGADO', incurriendo
           en un costo real de (longitud * factor_consumo_plegado), el cual es
           estrictamente mayor que el estimado por la heurística
           (longitud * factor_consumo_normal).

        3. Cota Inferior de Transformaciones: La heurística asume optimísticamente
           un costo de 0 en futuras transformaciones de chasis, mientras que en la
           realidad cualquier cambio de modo suma costo_transformacion >= 0.

        En conclusión, h(n) jamás sobreestima el costo energético real remanente h*(n),
        incluso cuando existen atajos estrechos o transformaciones, garantizando que A*
        sea 100% ADMISIBLE, CONSISTENTE (monótono) y encuentre el óptimo global.
        -------------------------------------------------------------------------

        Args:
            estado_actual: Estado compuesto (id_nodo, modo) que se está evaluando.
            nodo_meta_id: Identificador del nodo destino de rescate.

        Returns:
            Estimación heurística admisible en unidades de batería.
        """
        x_actual, y_actual = self.entorno.obtener_coordenadas(estado_actual.id_nodo)
        x_meta, y_meta = self.entorno.obtener_coordenadas(nodo_meta_id)

        distancia_euclidiana = math.hypot(x_meta - x_actual, y_meta - y_actual)
        return distancia_euclidiana * self.configuracion.factor_consumo_normal

    def _obtener_factor_consumo(self, modo: str) -> float:
        """
        Retorna el factor de consumo de batería por metro según el modo físico indicado.

        Args:
            modo: 'NORMAL' o 'PLEGADO'.

        Returns:
            Factor energético en unidades de batería por metro.
        """
        if modo == MODO_NORMAL:
            return self.configuracion.factor_consumo_normal
        if modo == MODO_PLEGADO:
            return self.configuracion.factor_consumo_plegado
        raise ValueError(f"Modo físico no reconocido: {modo}")

    def expandir_estado(self, estado_actual: EstadoRobot) -> List[TransicionEstado]:
        """
        Función sucesora del algoritmo A*.

        Dado un `estado_actual` (U, modo_actual):
        1. Genera transiciones de DESPLAZAMIENTO hacia todos los nodos vecinos V
           conectados a U, verificando primero si el `tipo_tunel` es transitable en
           `modo_actual` (impidiendo entrar en modo 'NORMAL' a túneles 'ESTRECHO').
        2. Genera siempre la transición de TRANSFORMACION permaneciendo en el mismo
           nodo U pero conmutando al modo físico opuesto y sumando `costo_transformacion`.

        Args:
            estado_actual: Estado (U, modo) que se desea expandir.

        Returns:
            Lista de objetos `TransicionEstado` alcanzables en un paso desde `estado_actual`.
        """
        sucesores: List[TransicionEstado] = []
        nodo_u = estado_actual.id_nodo
        modo_actual = estado_actual.modo
        factor_modo = self._obtener_factor_consumo(modo_actual)

        # 1. Transiciones de movimiento hacia nodos adyacentes V
        for conexion in self.entorno.obtener_vecinos(nodo_u):
            if not self.entorno.es_tunel_transitable(conexion.tipo_tunel, modo_actual):
                continue

            costo_movimiento = conexion.longitud_metros * factor_modo
            estado_vecino = EstadoRobot(
                id_nodo=conexion.nodo_vecino_id,
                modo=modo_actual,
            )
            sucesores.append(
                TransicionEstado(
                    estado_siguiente=estado_vecino,
                    costo_paso=costo_movimiento,
                    tipo_accion=ACCION_DESPLAZAMIENTO,
                    longitud_metros=conexion.longitud_metros,
                )
            )

        # 2. Transición de transformación de chasis en el mismo nodo U
        estado_transformado = estado_actual.transformar()
        sucesores.append(
            TransicionEstado(
                estado_siguiente=estado_transformado,
                costo_paso=self.configuracion.costo_transformacion,
                tipo_accion=ACCION_TRANSFORMACION,
                longitud_metros=0.0,
            )
        )

        return sucesores

    def buscar_ruta(
        self,
        nodo_origen_id: int,
        nodo_meta_id: int,
        modo_inicial: str = MODO_NORMAL,
    ) -> ResultadoBusquedaAStar:
        """
        Ejecuta el bucle central de búsqueda heurística óptima A* desde
        (`nodo_origen_id`, `modo_inicial`) hasta alcanzar `nodo_meta_id` en cualquier modo.

        Utiliza una cola de prioridad (`heapq`) para gestionar la Frontera ordenada por
        `f(n) = g(n) + h(n)` y un diccionario `costo_acumulado` para registrar el costo
        mínimo conocido `g(n)` de cada `EstadoRobot`, previniendo ciclos e ignorando
        entradas obsoletas en la frontera.

        Args:
            nodo_origen_id: Identificador del nodo inicial de la misión.
            nodo_meta_id: Identificador del nodo destino de rescate.
            modo_inicial: Configuración física inicial del robot ('NORMAL' o 'PLEGADO').

        Returns:
            Instancia de `ResultadoBusquedaAStar` con las métricas óptimas y la ruta paso a paso.
        """
        # Validación de existencia de nodos en memoria
        self.entorno.obtener_nodo(nodo_origen_id)
        self.entorno.obtener_nodo(nodo_meta_id)

        estado_inicial = EstadoRobot(id_nodo=nodo_origen_id, modo=modo_inicial)

        # Diccionario de costos óptimos conocidos g(n) para control de visitados y ciclos
        costo_acumulado: Dict[EstadoRobot, float] = {estado_inicial: 0.0}

        # Diccionario de predecesores para reconstrucción exacta del camino óptimo
        vino_de: Dict[EstadoRobot, Tuple[EstadoRobot, TransicionEstado]] = {}

        # Cola de prioridad (Frontera): almacena tuplas (f_score, g_score, EstadoRobot)
        f_inicial = self.calcular_heuristica(estado_inicial, nodo_meta_id)
        frontera: List[Tuple[float, float, EstadoRobot]] = [
            (f_inicial, 0.0, estado_inicial)
        ]

        estados_expandidos = 0

        while frontera:
            f_actual, g_actual, estado_actual = heapq.heappop(frontera)

            # Control de entradas obsoletas en el heap (lazy deletion):
            # Si ya encontramos un camino estrictamente mejor hacia estado_actual, descartar.
            if g_actual > costo_acumulado.get(estado_actual, float("inf")):
                continue

            # Condición de parada óptima: se extrajo el nodo meta de la cola de prioridad
            if estado_actual.id_nodo == nodo_meta_id:
                pasos_ruta = self._reconstruir_ruta(
                    estado_inicial=estado_inicial,
                    estado_meta=estado_actual,
                    vino_de=vino_de,
                )
                distancia_total = sum(p.distancia_paso_metros for p in pasos_ruta)
                total_transformaciones = sum(
                    1 for p in pasos_ruta if p.tipo_accion == ACCION_TRANSFORMACION
                )
                return ResultadoBusquedaAStar(
                    encontrada=True,
                    nodo_origen_id=nodo_origen_id,
                    nodo_meta_id=nodo_meta_id,
                    modo_inicial=modo_inicial,
                    modo_final=estado_actual.modo,
                    bateria_total_consumida=g_actual,
                    distancia_total_metros=distancia_total,
                    total_transformaciones=total_transformaciones,
                    estados_expandidos=estados_expandidos,
                    pasos_ruta=pasos_ruta,
                )

            estados_expandidos += 1

            # Expansión de sucesores (desplazamientos válidos + transformación intra-nodo)
            for transicion in self.expandir_estado(estado_actual):
                estado_vecino = transicion.estado_siguiente
                nuevo_g = g_actual + transicion.costo_paso

                if nuevo_g < costo_acumulado.get(estado_vecino, float("inf")):
                    costo_acumulado[estado_vecino] = nuevo_g
                    vino_de[estado_vecino] = (estado_actual, transicion)
                    h_vecino = self.calcular_heuristica(estado_vecino, nodo_meta_id)
                    nuevo_f = nuevo_g + h_vecino
                    heapq.heappush(frontera, (nuevo_f, nuevo_g, estado_vecino))

        # Caso sin solución (grafo desconectado o meta inaccesible)
        return ResultadoBusquedaAStar(
            encontrada=False,
            nodo_origen_id=nodo_origen_id,
            nodo_meta_id=nodo_meta_id,
            modo_inicial=modo_inicial,
            modo_final=None,
            bateria_total_consumida=0.0,
            distancia_total_metros=0.0,
            total_transformaciones=0,
            estados_expandidos=estados_expandidos,
            pasos_ruta=[],
        )

    @staticmethod
    def _reconstruir_ruta(
        estado_inicial: EstadoRobot,
        estado_meta: EstadoRobot,
        vino_de: Dict[EstadoRobot, Tuple[EstadoRobot, TransicionEstado]],
    ) -> List[PasoRutaOptima]:
        """
        Reconstruye en orden cronológico (paso 0 .. k) la secuencia óptima de estados
        y acciones desde `estado_inicial` hasta `estado_meta`.

        Args:
            estado_inicial: Estado de partida de la búsqueda.
            estado_meta: Estado final en el que se alcanzó el nodo meta.
            vino_de: Mapa de punteros hacia atrás generado durante la búsqueda A*.

        Returns:
            Lista secuencial de `PasoRutaOptima`.
        """
        transiciones_inversas: List[Tuple[EstadoRobot, TransicionEstado]] = []
        cursor_estado = estado_meta

        while cursor_estado != estado_inicial:
            estado_previo, transicion = vino_de[cursor_estado]
            transiciones_inversas.append((cursor_estado, transicion))
            cursor_estado = estado_previo

        transiciones_inversas.reverse()

        pasos: List[PasoRutaOptima] = [
            PasoRutaOptima(
                orden_paso=0,
                estado=estado_inicial,
                tipo_accion=ACCION_INICIO,
                costo_paso_bateria=0.0,
                bateria_acumulada=0.0,
                distancia_paso_metros=0.0,
            )
        ]

        bateria_acum = 0.0
        for indice, (estado_destino, transicion) in enumerate(
            transiciones_inversas, start=1
        ):
            bateria_acum += transicion.costo_paso
            pasos.append(
                PasoRutaOptima(
                    orden_paso=indice,
                    estado=estado_destino,
                    tipo_accion=transicion.tipo_accion,
                    costo_paso_bateria=transicion.costo_paso,
                    bateria_acumulada=bateria_acum,
                    distancia_paso_metros=transicion.longitud_metros,
                )
            )

        return pasos

