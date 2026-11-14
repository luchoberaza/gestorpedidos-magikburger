"""Stock de insumos.

Cada producto puede tener una receta (qué insumos consume por unidad) y cada
ingrediente puede estar ligado a un insumo: si se agrega como extra se consume
una porción; si se pide «sin …», el producto deja de llevar ese insumo.

El descuento real se aplica al cerrar la jornada, sobre los pedidos cerrados,
así editar o borrar un pedido durante el día nunca deja el stock desfasado.
Mientras tanto, lo que piden los pedidos abiertos se muestra como
"comprometido" y el disponible es stock − comprometido.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..db import ahora_iso


def recetas_y_ligados(conn: sqlite3.Connection) -> Tuple[Dict[int, Dict[int, float]], Dict[int, Tuple[int, float]]]:
    """({product_id: {insumo_id: cantidad}}, {ingredient_id: (insumo_id, cantidad)}), en la unidad de compra."""
    recetas: Dict[int, Dict[int, float]] = {}
    for r in conn.execute("SELECT product_id, insumo_id, cantidad FROM recetas;").fetchall():
        d = recetas.setdefault(int(r["product_id"]), {})
        d[int(r["insumo_id"])] = d.get(int(r["insumo_id"]), 0.0) + float(r["cantidad"] or 0)
    ligados = {int(r["ingredient_id"]): (int(r["insumo_id"]), float(r["cantidad"] or 0))
               for r in conn.execute("SELECT ingredient_id, insumo_id, cantidad FROM ingrediente_insumo;").fetchall()}
    return recetas, ligados


def aplicar_mods(receta: Dict[int, float], ligados: Dict[int, Tuple[int, float]], mods: List[Any],
                 cuenta: Optional[Callable[[int], bool]] = None) -> Tuple[Dict[int, float], List[Any]]:
    """Lo que lleva UNA unidad vendida, con sus cambios de la caja. La misma regla para el stock y el costo.

    · «Sin X»: el producto deja de llevar la materia prima de X (lo que diga su receta).
    · Extra X: suma lo que lleva una porción de X (cantidad del ingrediente). Si X no tiene
      materia prima o cantidad —o `cuenta` dice que no—, no suma nada y vuelve en la lista.

    mods: [(kind, ingredient_id, ...)]. Devuelve (insumo → cantidad, extras que no se contaron)."""
    por_item = dict(receta)
    for m in mods:
        if m[0] == "remove" and int(m[1]) in ligados:
            insumo = ligados[int(m[1])][0]
            if insumo in por_item:
                por_item[insumo] = 0.0
    sin_contar: List[Any] = []
    for m in mods:
        if m[0] != "add":
            continue
        insumo, cant = ligados.get(int(m[1]), (None, 0.0))
        if insumo is None or cant <= 0 or (cuenta is not None and not cuenta(insumo)):
            sin_contar.append(m)
            continue
        por_item[insumo] = por_item.get(insumo, 0.0) + cant
    return por_item, sin_contar


def mods_por_item(conn: sqlite3.Connection, ids: List[int]) -> Dict[int, List[Any]]:
    """order_item_id → [(kind, ingredient_id, price_cents, nombre)]."""
    mods: Dict[int, List[Any]] = {}
    for lote in range(0, len(ids), 500):
        parte = ids[lote:lote + 500]
        for m in conn.execute(
                f"SELECT order_item_id, kind, ingredient_id, price_cents, name_snapshot FROM order_item_mods "
                f"WHERE order_item_id IN ({','.join('?' * len(parte))}) ORDER BY id;", parte).fetchall():
            mods.setdefault(int(m["order_item_id"]), []).append(
                ((m["kind"] or "").strip(), int(m["ingredient_id"] or 0), int(m["price_cents"] or 0),
                 m["name_snapshot"] or ""))
    return mods


def consumo_de_pedidos(conn: sqlite3.Connection, where_sql: str, params: List[Any]) -> Dict[int, float]:
    """insumo_id -> cantidad que consumen los pedidos que cumplen `where_sql` (alias o)."""
    recetas, ligados = recetas_y_ligados(conn)
    if not recetas and not ligados:
        return {}

    items = conn.execute(
        "SELECT i.id, i.product_id, i.qty FROM order_items i JOIN orders o ON o.id = i.order_id "
        f"WHERE {where_sql};", params).fetchall()
    if not items:
        return {}
    mods = mods_por_item(conn, [int(i["id"]) for i in items])

    total: Dict[int, float] = {}
    for it in items:
        qty = int(it["qty"] or 1)
        por_item, _ = aplicar_mods(recetas.get(int(it["product_id"]), {}), ligados, mods.get(int(it["id"]), []))
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


def convertir_unidad(conn: sqlite3.Connection, insumo_id: int, vieja: str, nueva: str,
                     costo: bool = True) -> Optional[float]:
    """Pasa un insumo de kg a g (o de l a ml, y al revés) sin que cambie lo que hay: el stock, el mínimo,
    las recetas, los extras y —si `costo`— el costo por unidad. Devuelve el factor, o None si no se
    puede convertir (ej.: de unidades a kg); en ese caso no toca nada."""
    from .unidades import conversion

    f = conversion(vieja, nueva)
    if f is None or f == 1.0:
        return f
    conn.execute("UPDATE insumos SET stock=ROUND(stock*?, 3), stock_minimo=ROUND(stock_minimo*?, 3) WHERE id=?;",
                 (f, f, insumo_id))
    if costo:
        conn.execute("UPDATE insumos SET costo_unit_cents=CAST(ROUND(costo_unit_cents/?) AS INTEGER) WHERE id=?;",
                     (f, insumo_id))
    conn.execute("UPDATE recetas SET cantidad=ROUND(cantidad*?, 6) WHERE insumo_id=?;", (f, insumo_id))
    conn.execute("UPDATE ingrediente_insumo SET cantidad=ROUND(cantidad*?, 6) WHERE insumo_id=?;", (f, insumo_id))
    return f


def descontar_cierre(conn: sqlite3.Connection, cierre_id: int, usuario: Optional[Dict[str, Any]]) -> int:
    """Aplica el consumo de los pedidos de un cierre. Devuelve cuántos insumos movió."""
    consumo = consumo_de_pedidos(conn, "o.cierre_id = ?", [cierre_id])
    for insumo, cant in consumo.items():
        if conn.execute("SELECT 1 FROM insumos WHERE id=?;", (insumo,)).fetchone():
            mover(conn, insumo, -cant, "consumo", f"Consumo de la jornada (cierre #{cierre_id})",
                  usuario, cierre_id=cierre_id)
    return len(consumo)
