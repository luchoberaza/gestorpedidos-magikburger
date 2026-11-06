"""Liquidación: el efectivo que rinde cada repartidor. UNA sola fórmula.

La usan la pantalla de Liquidación y el PDF del cierre, para que nunca puedan
decir números distintos.

Reglas del negocio (las mismas que venía aplicando la app):
  · Solo los pedidos ENTREGADOS cuentan como cobrados.
  · Lo que no es efectivo (transferencia y los medios que no sean efectivo) no
    se rinde: entró directo a la cuenta del local.
  · El envío se lo queda el repartidor: se descuenta de lo que rinde.
  · El cambio se devuelve al cierre: se suma una vez por repartidor que salió.
  · Los pedidos sin repartidor (retiro en el local) son una fila más, sin cambio.

Alcance: los pedidos ABIERTOS (todavía sin cierre) de la jornada actual. La
jornada empieza a la hora de corte configurada (por defecto 06:00), así un
pedido de la 01:00 cuenta para la noche anterior y no queda "arrastrado".
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from .. import config
from .pedidos import envios_por_pedido

ESTADOS = ["new", "kitchen", "way", "done"]
ESTADOS_ETIQUETA = {"new": "Nuevo", "kitchen": "En cocina", "way": "En camino", "done": "Entregado"}
ENTREGADOS = {"done"}
SIN_REPARTIDOR = "Sin repartidor"


def corte(conn: sqlite3.Connection) -> int:
    return int(config.obtener(conn, "hora_corte_jornada"))


def jornada_de(momento: datetime, horas_corte: int) -> str:
    return (momento - timedelta(hours=horas_corte)).date().isoformat()


def jornada_actual(conn: sqlite3.Connection, ahora: Optional[datetime] = None) -> str:
    return jornada_de(ahora or datetime.now(), corte(conn))


def sql_jornada(conn: sqlite3.Connection, col: str = "o.created_at") -> str:
    """Expresión SQL que da la jornada (YYYY-MM-DD) de un pedido."""
    return f"date(replace({col}, 'T', ' '), '-{corte(conn)} hours')"


def es_transferencia_por_texto(pm: Any) -> bool:
    """Criterio por texto para códigos que no están en la tabla de medios de pago."""
    s = (str(pm or "")).strip().lower()
    if not s:
        return False
    if s in {"mp", "transf", "transfer", "transferencia", "banco", "mercadopago"}:
        return True
    return s.startswith("transf") or "transfer" in s or "banco" in s or "mercado" in s


def medios_de_pago(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    try:
        for r in conn.execute("SELECT * FROM metodos_pago ORDER BY sort_order, id;").fetchall():
            out[r["code"]] = dict(r)
    except sqlite3.Error:
        pass
    return out


def es_efectivo(medios: Dict[str, Dict[str, Any]], code: Any) -> bool:
    m = medios.get(str(code or ""))
    if m is not None:
        return bool(m["is_cash"])
    return not es_transferencia_por_texto(code)


def nombre_medio(medios: Dict[str, Dict[str, Any]], code: Any) -> str:
    m = medios.get(str(code or ""))
    if m is not None:
        return m["name"]
    return "Transferencia" if es_transferencia_por_texto(code) else "Efectivo"


def _cambio(conn: sqlite3.Connection, courier_id: int) -> int:
    r = conn.execute("SELECT cash_float_cents FROM couriers WHERE id=?;", (courier_id,)).fetchone()
    if r is not None and r["cash_float_cents"] is not None:
        return int(r["cash_float_cents"])
    return int(config.obtener(conn, "cambio_default_cents"))


def calcular(conn: sqlite3.Connection, jornada: Optional[str] = None,
             cierre_id: Optional[int] = None) -> Dict[str, Any]:
    """Liquidación de los pedidos abiertos de `jornada` (None = todos los abiertos),
    o de los pedidos de un cierre ya hecho si se pasa `cierre_id`."""
    where, params = [], []
    if cierre_id is not None:
        where.append("o.cierre_id = ?")
        params.append(cierre_id)
    else:
        where.append("o.cierre_id IS NULL")
        if jornada:
            where.append(f"{sql_jornada(conn)} = ?")
            params.append(jornada)
    pedidos = conn.execute(
        "SELECT o.id, o.total_cents, o.courier_id, o.payment_method, o.status, c.name AS courier_name "
        "FROM orders o LEFT JOIN couriers c ON c.id = o.courier_id WHERE " + " AND ".join(where) + ";",
        params,
    ).fetchall()

    envios = envios_por_pedido(conn, [int(p["id"]) for p in pedidos])
    medios = medios_de_pago(conn)
    filas: Dict[Any, Dict[str, Any]] = {}
    por_medio: Dict[str, int] = {}

    for o in pedidos:
        cid = int(o["courier_id"]) if o["courier_id"] is not None else None
        nombre = (o["courier_name"] or SIN_REPARTIDOR).strip() if cid is not None else SIN_REPARTIDOR
        clave = cid if cid is not None else SIN_REPARTIDOR
        f = filas.get(clave)
        if f is None:
            f = filas[clave] = {
                "courier_id": cid, "courier_name": nombre,
                "orders_count": 0, "delivered_count": 0, "gross_total_cents": 0,
                "cash_cents": 0, "transfer_cents": 0, "delivery_cents": 0,
                "delivery_all_cents": 0, "pending_cents": 0, "pending_count": 0,
                "cash_float_cents": 0,
            }
        total = int(o["total_cents"] or 0)
        envio = envios.get(int(o["id"]), 0)
        f["orders_count"] += 1
        f["gross_total_cents"] += total
        if (o["status"] or "new").strip().lower() not in ENTREGADOS:
            f["pending_count"] += 1
            f["pending_cents"] += total
            continue
        f["delivered_count"] += 1
        f["delivery_all_cents"] += envio
        medio = nombre_medio(medios, o["payment_method"])
        por_medio[medio] = por_medio.get(medio, 0) + total
        if es_efectivo(medios, o["payment_method"]):
            f["cash_cents"] += total
            f["delivery_cents"] += envio
        else:
            f["transfer_cents"] += total

    for f in filas.values():
        if f["courier_id"] is not None and f["orders_count"] > 0:
            f["cash_float_cents"] = _cambio(conn, f["courier_id"])
        f["to_render_cents"] = f["cash_cents"] - f["delivery_cents"] + f["cash_float_cents"]

    orden = sorted(filas.values(), key=lambda f: (f["courier_id"] is None, (f["courier_name"] or "").lower()))

    def suma(campo: str) -> int:
        return sum(int(f[campo]) for f in orden)

    return {
        "jornada": jornada,
        "couriers": orden,
        "por_medio": [{"medio": k, "total_cents": v} for k, v in sorted(por_medio.items())],
        "orders_count": suma("orders_count"),
        "delivered_count": suma("delivered_count"),
        "gross_total_cents": suma("gross_total_cents"),
        "cash_cents": suma("cash_cents"),
        "transfer_cents": suma("transfer_cents"),
        "delivery_cents": suma("delivery_cents"),
        "delivery_all_cents": suma("delivery_all_cents"),
        "cash_float_total_cents": suma("cash_float_cents"),
        "pending_cents": suma("pending_cents"),
        "pending_count": suma("pending_count"),
        # El total es la suma exacta de las filas: no puede discrepar del detalle.
        "cash_to_render_cents": suma("to_render_cents"),
    }


def arrastrados(conn: sqlite3.Connection, jornada: str) -> Dict[str, int]:
    """Pedidos abiertos de jornadas ANTERIORES: no se suman a hoy, pero se avisan."""
    r = conn.execute(
        f"SELECT COUNT(*) AS n, COALESCE(SUM(o.total_cents), 0) AS t FROM orders o "
        f"WHERE o.cierre_id IS NULL AND {sql_jornada(conn)} < ?;", (jornada,)).fetchone()
    return {"count": int(r["n"] or 0), "total_cents": int(r["t"] or 0)}
