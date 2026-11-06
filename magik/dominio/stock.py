"""Stock de insumos.

Cada producto puede tener una receta (qué insumos consume por unidad) y cada
ingrediente puede estar ligado a un insumo: si se agrega como extra se consume,
si se quita de la base se deja de consumir.

El descuento real se aplica al cerrar la jornada, sobre los pedidos cerrados,
así editar o borrar un pedido durante el día nunca deja el stock desfasado.
Mientras tanto, lo que piden los pedidos abiertos se muestra como
"comprometido" y el disponible es stock − comprometido.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from ..db import ahora_iso


def consumo_de_pedidos(conn: sqlite3.Connection, where_sql: str, params: List[Any]) -> Dict[int, float]:
    """insumo_id -> cantidad que consumen los pedidos que cumplen `where_sql` (alias o)."""
    recetas: Dict[int, List[Any]] = {}
    for r in conn.execute("SELECT product_id, insumo_id, cantidad FROM recetas;").fetchall():
        recetas.setdefault(int(r["product_id"]), []).append((int(r["insumo_id"]), float(r["cantidad"] or 0)))
    ligados = {int(r["ingredient_id"]): (int(r["insumo_id"]), float(r["cantidad"] or 0))
               for r in conn.execute("SELECT ingredient_id, insumo_id, cantidad FROM ingrediente_insumo;").fetchall()}
    if not recetas and not ligados:
        return {}

    items = conn.execute(
        "SELECT i.id, i.product_id, i.qty FROM order_items i JOIN orders o ON o.id = i.order_id "
        f"WHERE {where_sql};", params).fetchall()
    if not items:
        return {}
    ids = [int(i["id"]) for i in items]
    mods: Dict[int, List[Any]] = {}
    for lote in range(0, len(ids), 500):
        parte = ids[lote:lote + 500]
        for m in conn.execute(
                f"SELECT order_item_id, kind, ingredient_id FROM order_item_mods "
                f"WHERE order_item_id IN ({','.join('?' * len(parte))});", parte).fetchall():
            mods.setdefault(int(m["order_item_id"]), []).append((m["kind"], int(m["ingredient_id"] or 0)))

    total: Dict[int, float] = {}
    for it in items:
        qty = int(it["qty"] or 1)
        por_item: Dict[int, float] = {}
        for insumo, cant in recetas.get(int(it["product_id"]), []):
            por_item[insumo] = por_item.get(insumo, 0.0) + cant
        for kind, ing in mods.get(int(it["id"]), []):
            if ing not in ligados:
                continue
            insumo, cant = ligados[ing]
            if kind == "add":
                por_item[insumo] = por_item.get(insumo, 0.0) + cant
            elif kind == "remove":
                por_item[insumo] = max(0.0, por_item.get(insumo, 0.0) - cant)
        for insumo, cant in por_item.items():
            if cant:
                total[insumo] = total.get(insumo, 0.0) + cant * qty
    return {k: round(v, 3) for k, v in total.items() if v}


def comprometido(conn: sqlite3.Connection) -> Dict[int, float]:
    return consumo_de_pedidos(conn, "o.cierre_id IS NULL", [])


def mover(conn: sqlite3.Connection, insumo_id: int, cantidad: float, tipo: str, motivo: str,
          usuario: Optional[Dict[str, Any]] = None, cierre_id: Optional[int] = None,
          fecha: Optional[str] = None, origen: str = "", origen_id: str = "") -> float:
    """Suma `cantidad` (con signo) al stock y deja el movimiento. Devuelve el stock nuevo."""
    fila = conn.execute("SELECT stock FROM insumos WHERE id=?;", (insumo_id,)).fetchone()
    if fila is None:
        raise ValueError("El insumo no existe.")
    nuevo = round(float(fila["stock"] or 0) + float(cantidad), 3)
    conn.execute("UPDATE insumos SET stock=? WHERE id=?;", (nuevo, insumo_id))
    conn.execute(
        "INSERT INTO movimientos_stock(fecha, insumo_id, cantidad, tipo, motivo, stock_resultante, "
        "cierre_id, user_id, user_name, origen, origen_id) VALUES(?,?,?,?,?,?,?,?,?,?,?);",
        (fecha or ahora_iso(), insumo_id, round(float(cantidad), 3), tipo, (motivo or "")[:160], nuevo,
         cierre_id, usuario["id"] if usuario else None, usuario["name"] if usuario else "",
         origen, origen_id),
    )
    return nuevo


def descontar_cierre(conn: sqlite3.Connection, cierre_id: int, usuario: Optional[Dict[str, Any]]) -> int:
    """Aplica el consumo de los pedidos de un cierre. Devuelve cuántos insumos movió."""
    consumo = consumo_de_pedidos(conn, "o.cierre_id = ?", [cierre_id])
    for insumo, cant in consumo.items():
        if conn.execute("SELECT 1 FROM insumos WHERE id=?;", (insumo,)).fetchone():
            mover(conn, insumo, -cant, "consumo", f"Consumo de la jornada (cierre #{cierre_id})",
                  usuario, cierre_id=cierre_id)
    return len(consumo)
