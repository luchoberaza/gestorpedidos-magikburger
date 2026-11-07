"""Cuánto deja cada producto (P-013): precio − costo de su receta.

El costo sale de lo que el local carga en Inventario: el costo unitario de cada insumo y la
receta de cada producto (qué insumos lleva y cuánto). Si a un producto le falta la receta,
o a un insumo de la receta le falta el costo, no se inventa nada: queda marcado para cargarlo.

Se usan los costos de hoy (no hay historial de costos) y lo cobrado en cada venta. Los extras
se cobran y se costean aparte, así que no entran. Las promos van en su propia fila: su precio
contra la suma de las recetas de lo que traen.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from .liquidacion import sql_jornada


def _rango(desde: Optional[str], hasta: Optional[str]) -> Dict[str, str]:
    return {"desde": desde or "0000-01-01", "hasta": hasta or "9999-12-31"}


def costos_por_producto(conn: sqlite3.Connection) -> Dict[int, Dict[str, Any]]:
    """{product_id: {"costo_cents": int, "faltan": [insumos sin costo]}} para los que tienen receta."""
    acum: Dict[int, Dict[str, Any]] = {}
    for f in conn.execute("SELECT r.product_id, r.cantidad, i.nombre, i.costo_unit_cents FROM recetas r "
                          "JOIN insumos i ON i.id = r.insumo_id ORDER BY i.nombre;").fetchall():
        d = acum.setdefault(int(f["product_id"]), {"costo": 0.0, "faltan": []})
        costo = int(f["costo_unit_cents"] or 0)
        if costo <= 0:
            d["faltan"].append(f["nombre"])
        d["costo"] += float(f["cantidad"] or 0) * costo
    return {pid: {"costo_cents": int(round(d["costo"])), "faltan": d["faltan"]} for pid, d in acum.items()}


def _fila(nombre: str, precio: int, costo: Optional[int], faltan: List[str], estado: str, vendidas: int,
          cobrado: int, promo: bool) -> Dict[str, Any]:
    conocido = estado == "ok" and costo is not None
    deja = precio - costo if conocido else None
    return {
        "nombre": nombre, "promo": promo, "estado": estado, "faltan": faltan,
        "precio_cents": precio, "costo_cents": costo if conocido else None, "deja_cents": deja,
        "margen": round(deja * 100 / precio) if conocido and precio > 0 else None,
        "vendidas": vendidas, "cobrado_cents": cobrado,
        "ganancia_cents": cobrado - costo * vendidas if conocido else None,
    }


def ganancia_por_producto(conn: sqlite3.Connection, desde: Optional[str], hasta: Optional[str]) -> Dict[str, Any]:
    r = _rango(desde, hasta)
    costos = costos_por_producto(conn)
    j = sql_jornada(conn)
    ventas = conn.execute(
        f"SELECT i.order_id, i.product_id, i.qty, i.promo_group, i.promo_id, i.promo_name_snapshot, "
        f"i.promo_price_cents, COALESCE(NULLIF(i.product_price_cents_snapshot, 0), i.base_price_cents) AS base "
        f"FROM order_items i JOIN orders o ON o.id = i.order_id WHERE {j} BETWEEN ? AND ?;",
        (r["desde"], r["hasta"])).fetchall()

    sueltas: Dict[int, Dict[str, int]] = {}
    promos: Dict[Any, Dict[str, Any]] = {}
    grupos_vistos = set()
    for v in ventas:
        qty = int(v["qty"] or 1)
        if v["promo_group"]:
            clave = int(v["promo_id"] or 0) or ("nombre", v["promo_name_snapshot"] or "Promo")
            p = promos.setdefault(clave, {"vendidas": 0, "cobrado": 0, "nombre": v["promo_name_snapshot"] or "Promo"})
            p["cobrado"] += int(v["promo_price_cents"] or 0)  # el precio viene en un solo componente
            if (v["order_id"], v["promo_group"]) not in grupos_vistos:
                grupos_vistos.add((v["order_id"], v["promo_group"]))
                p["vendidas"] += 1
        else:
            s = sueltas.setdefault(int(v["product_id"]), {"vendidas": 0, "cobrado": 0})
            s["vendidas"] += qty
            s["cobrado"] += int(v["base"] or 0) * qty

    def estado_de(pid: int) -> Any:
        c = costos.get(pid)
        if c is None:
            return None, [], "sin_receta"
        return c["costo_cents"], c["faltan"], ("sin_costo" if c["faltan"] else "ok")

    filas: List[Dict[str, Any]] = []
    for p in conn.execute("SELECT id, name, price_cents, active FROM products;").fetchall():
        pid = int(p["id"])
        s = sueltas.get(pid, {"vendidas": 0, "cobrado": 0})
        if not int(p["active"]) and not s["vendidas"]:
            continue
        costo, faltan, estado = estado_de(pid)
        filas.append(_fila(p["name"], int(p["price_cents"] or 0), costo, faltan, estado,
                           s["vendidas"], s["cobrado"], False))

    actuales = {int(p["id"]): p for p in conn.execute("SELECT id, name, price_cents, active FROM promos;").fetchall()}
    comps: Dict[int, List[int]] = {}
    for c in conn.execute("SELECT promo_id, product_id FROM promo_components ORDER BY sort_order, id;").fetchall():
        comps.setdefault(int(c["promo_id"]), []).append(int(c["product_id"]))
    for clave in set(actuales) | set(promos):
        venta = promos.get(clave, {"vendidas": 0, "cobrado": 0, "nombre": "Promo"})
        actual = actuales.get(clave) if isinstance(clave, int) else None
        if actual is not None and not int(actual["active"]) and not venta["vendidas"]:
            continue
        costo, faltan, estado = (0, [], "ok") if actual is not None and comps.get(clave) else (None, [], "sin_receta")
        for pid in (comps.get(clave, []) if actual is not None else []):
            c, f, e = estado_de(pid)
            if e == "sin_receta":
                costo, estado = None, "sin_receta"
                break
            costo += c
            faltan += [x for x in f if x not in faltan]
            if e == "sin_costo":
                estado = "sin_costo"
        precio = int(actual["price_cents"] or 0) if actual is not None else (
            round(venta["cobrado"] / venta["vendidas"]) if venta["vendidas"] else 0)
        filas.append(_fila(actual["name"] if actual is not None else venta["nombre"], precio, costo, faltan,
                           estado, venta["vendidas"], venta["cobrado"], True))

    filas.sort(key=lambda f: (f["ganancia_cents"] is None, -(f["ganancia_cents"] or 0), -f["vendidas"],
                              f["nombre"].lower()))
    conocidas = [f for f in filas if f["ganancia_cents"] is not None]
    ganancia = sum(f["ganancia_cents"] for f in conocidas)
    cobrado = sum(f["cobrado_cents"] for f in conocidas)
    return {
        "filas": filas,
        "ganancia_cents": ganancia,
        "cobrado_con_costo_cents": cobrado,
        "margen": round(ganancia * 100 / cobrado) if cobrado > 0 else None,
        "sin_costo": sum(1 for f in filas if f["estado"] != "ok"),
        "hay_recetas": bool(costos),
    }
