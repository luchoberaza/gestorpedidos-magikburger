"""Configuración del negocio (tabla `settings`, clave/valor).

Todo lo que antes estaba escrito en el código —nombre del negocio, textos del
ticket, cambio de los repartidores, categorías de gasto, etc.— vive acá, con un
valor por defecto y una validación. Los valores por defecto son los que la app
usaba hasta ahora, así que actualizar no cambia nada que el local no toque.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

CATEGORIAS_GASTO = [
    "Ingredientes", "Carne", "Pan", "Papas", "Bebidas", "Queso", "Salsas", "Packaging",
    "Gas", "Electricidad", "Agua", "Internet", "Alquiler", "Sueldos", "Combustible",
    "Publicidad", "Mantenimiento", "Otros",
]
UNIDADES = ["unidades", "kg", "g", "l", "ml", "paquetes", "cajas", "fetas", "porciones"]


def _entero(minimo: Optional[int] = None, maximo: Optional[int] = None) -> Callable[[Any], int]:
    def f(v: Any) -> int:
        try:
            n = int(round(float(str(v).strip().replace(",", "."))))
        except (TypeError, ValueError):
            raise ValueError("tiene que ser un número")
        if minimo is not None and n < minimo:
            raise ValueError(f"no puede ser menor que {minimo}")
        if maximo is not None and n > maximo:
            raise ValueError(f"no puede ser mayor que {maximo}")
        return n
    return f


def _texto(maximo: int = 120, obligatorio: bool = False) -> Callable[[Any], str]:
    def f(v: Any) -> str:
        s = str(v if v is not None else "").strip()
        if obligatorio and not s:
            raise ValueError("no puede quedar vacío")
        return s[:maximo]
    return f


def _bool(v: Any) -> int:
    if isinstance(v, str):
        return 1 if v.strip().lower() in {"1", "true", "si", "sí", "on", "yes"} else 0
    return 1 if v else 0


def _lista(v: Any) -> str:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            v = [x for x in v.split(",")]
    if not isinstance(v, list):
        raise ValueError("tiene que ser una lista")
    vistos, out = set(), []
    for x in v:
        s = str(x).strip()[:40]
        if s and s.lower() not in vistos:
            vistos.add(s.lower())
            out.append(s)
    return json.dumps(out, ensure_ascii=False)


def _tema(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s not in {"oscuro", "claro"}:
        raise ValueError("tiene que ser 'oscuro' o 'claro'")
    return s


def _usuario_o_vacio(v: Any) -> str:
    s = str(v if v is not None else "").strip()
    if s == "":
        return ""
    try:
        return str(int(s))
    except ValueError:
        raise ValueError("usuario inválido")


# clave -> (valor por defecto, validador, es_lista, etiqueta)
REGISTRO: Dict[str, Tuple[Any, Callable[[Any], Any], bool, str]] = {
    "negocio_nombre": ("MagikBurger", _texto(60, True), False, "Nombre del negocio"),
    "negocio_direccion": ("", _texto(120), False, "Dirección del local"),
    "negocio_telefono": ("", _texto(40), False, "Teléfono del local"),
    "moneda_simbolo": ("$", _texto(4, True), False, "Símbolo de moneda"),
    "tema": ("oscuro", _tema, False, "Tema"),
    "ticket_encabezado": ("Magik", _texto(30), False, "Título del ticket"),
    "ticket_pie": ("Gracias", _texto(80), False, "Pie del ticket"),
    "ticket_copies": (1, _entero(1, 10), False, "Copias del ticket"),
    "ticket_logo": (1, _bool, False, "Imprimir logo en el ticket"),
    "ticket_mostrar_telefono_local": (0, _bool, False, "Mostrar teléfono del local en el ticket"),
    "printer_name": ("", _texto(120), False, "Impresora de tickets"),
    "delivery_fee_cents": (0, _entero(0), False, "Envío por defecto"),
    "cambio_default_cents": (150000, _entero(0), False, "Cambio por defecto de un repartidor"),
    "hora_corte_jornada": (6, _entero(0, 12), False, "Hora de corte de la jornada"),
    "umbral_gasto_cents": (3000000, _entero(0), False, "Aviso de gasto elevado"),
    "categorias_gasto": (CATEGORIAS_GASTO, _lista, True, "Categorías de gasto"),
    "unidades_stock": (UNIDADES, _lista, True, "Unidades de stock"),
    "recordatorio_dias_aviso": (5, _entero(0, 31), False, "Días de aviso de pagos"),
    "acceso_libre_usuario_id": ("", _usuario_o_vacio, False, "Usuario de acceso libre"),
    "respaldo_auto": (1, _bool, False, "Respaldo automático"),
    "respaldos_a_conservar": (30, _entero(3, 365), False, "Respaldos a conservar"),
}

# Claves internas que nunca salen hacia la pantalla.
INTERNAS = {"secret_key"}


def _leer_crudo(conn: sqlite3.Connection, clave: str) -> Optional[str]:
    try:
        r = conn.execute("SELECT value FROM settings WHERE key=?;", (clave,)).fetchone()
        return r["value"] if r and r["value"] is not None else None
    except sqlite3.Error:
        return None


def _escribir(conn: sqlite3.Connection, clave: str, valor: Any) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
        (clave, str(valor)),
    )


def obtener(conn: sqlite3.Connection, clave: str) -> Any:
    defecto, validador, es_lista, _ = REGISTRO[clave]
    crudo = _leer_crudo(conn, clave)
    if crudo is None:
        return list(defecto) if es_lista else defecto
    try:
        if es_lista:
            return json.loads(validador(crudo))
        return validador(crudo)
    except (ValueError, TypeError):
        return list(defecto) if es_lista else defecto


def todas(conn: sqlite3.Connection) -> Dict[str, Any]:
    return {k: obtener(conn, k) for k in REGISTRO}


def guardar(conn: sqlite3.Connection, datos: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Valida y guarda. Devuelve (lo guardado, errores legibles)."""
    guardado, errores = {}, []
    for clave, valor in (datos or {}).items():
        if clave not in REGISTRO:
            continue
        _, validador, es_lista, etiqueta = REGISTRO[clave]
        try:
            v = validador(valor)
        except ValueError as e:
            errores.append(f"{etiqueta}: {e}.")
            continue
        _escribir(conn, clave, v)
        guardado[clave] = json.loads(v) if es_lista else v
    return guardado, errores


def secret_key(conn: sqlite3.Connection) -> str:
    """Clave de las sesiones: se genera una vez por instalación."""
    k = _leer_crudo(conn, "secret_key")
    if not k:
        k = secrets.token_hex(32)
        _escribir(conn, "secret_key", k)
        conn.commit()
    return k
