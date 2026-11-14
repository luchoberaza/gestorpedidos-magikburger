"""Cuánto deja cada producto (P-013) y la ganancia real de lo vendido (P-015).

El costo sale de la materia prima (lo que se compra y cuánto vale) y de lo que lleva cada
producto: lo mismo que se carga en Inventario o con la planilla de costos de Configuración.
Si a un producto le falta la receta, o a una materia prima de la receta le falta el costo,
no se inventa nada: queda marcado para cargarlo.

La ganancia del período es la real de cada venta (P-015, responsable: "si la hamburguesa […]
se le agrega un ingrediente y ese ingrediente tiene que tener un costo […] se le suma una
carne […] no puede perderse"):
  · un extra cobrado suma su precio y el costo de su porción (Extra carne = una hamburguesa);
  · «sin X» descuenta el costo de lo que se sacó;
  · un extra sin materia prima o sin costo no se cuenta (ni lo cobrado ni el costo) y se avisa.
Es la misma regla con la que se descuenta el stock (`stock.aplicar_mods`).

Se usan los costos de hoy (no hay historial de costos) y lo cobrado en cada venta. Las promos
van en su propia fila: su precio contra la suma de lo que llevan sus productos.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from . import stock
from .liquidacion import sql_jornada


def _rango(desde: Optional[str], hasta: Optional[str]) -> Dict[str, str]:
    return {"desde": desde or "0000-01-01", "hasta": hasta or "9999-12-31"}


def _insumos(conn: sqlite3.Connection) -> Dict[int, Dict[str, Any]]:
    return {int(i["id"]): {"nombre": i["nombre"], "costo": int(i["costo_unit_cents"] or 0)}
            for i in conn.execute("SELECT id, nombre, costo_unit_cents FROM insumos;").fetchall()}


def _costo(lineas: Dict[int, float], insumos: Dict[int, Dict[str, Any]]) -> float:
    return sum(float(q) * insumos[x]["costo"] for x, q in lineas.items() if x in insumos and q)


def costos_por_producto(conn: sqlite3.Connection) -> Dict[int, Dict[str, Any]]:
    """{product_id: {"costo_cents": int, "faltan": [insumos sin costo]}} para los que tienen receta."""
    insumos = _insumos(conn)
    recetas, _ = stock.recetas_y_ligados(conn)
    out: Dict[int, Dict[str, Any]] = {}
    for pid, lineas in recetas.items():
        faltan = sorted({insumos[x]["nombre"] for x, q in lineas.items() if x in insumos and insumos[x]["costo"] <= 0},
                        key=str.lower)
        out[pid] = {"costo_cents": int(round(_costo(lineas, insumos))), "faltan": faltan}
    return out


def _fila(nombre: str, precio: int, costo: Optional[int], faltan: List[str], estado: str, vendidas: int,
          cobrado: float, promo: bool, costo_real: Optional[float], extras: int = 0) -> Dict[str, Any]:
    conocido = estado == "ok" and costo is not None
    deja = precio - costo if conocido else None
    cobrado = int(round(cobrado))
    return {
        "nombre": nombre, "promo": promo, "estado": estado, "faltan": faltan,
        "precio_cents": precio, "costo_cents": costo if conocido else None, "deja_cents": deja,
        "margen": round(deja * 100 / precio) if conocido and precio > 0 else None,
        "vendidas": vendidas, "cobrado_cents": cobrado, "extras_cents": int(extras),
        "ganancia_cents": int(round(cobrado - costo_real)) if costo_real is not None else None,
    }


def ganancia_por_producto(conn: sqlite3.Connection, desde: Optional[str], hasta: Optional[str]) -> Dict[str, Any]:
    r = _rango(desde, hasta)
    insumos = _insumos(conn)
    recetas, ligados = stock.recetas_y_ligados(conn)
    costos = costos_por_producto(conn)
    j = sql_jornada(conn)
    ventas = conn.execute(
        f"SELECT i.id, i.order_id, i.product_id, i.qty, i.promo_group, i.promo_id, i.promo_name_snapshot, "
        f"i.promo_price_cents, COALESCE(NULLIF(i.product_price_cents_snapshot, 0), i.base_price_cents) AS base "
        f"FROM order_items i JOIN orders o ON o.id = i.order_id WHERE {j} BETWEEN ? AND ?;",
        (r["desde"], r["hasta"])).fetchall()
    mods = stock.mods_por_item(conn, [int(v["id"]) for v in ventas])

    def con_costo(insumo: int) -> bool:
        return insumo in insumos and insumos[insumo]["costo"] > 0

    def ok_producto(pid: int) -> bool:
        c = costos.get(pid)
        return c is not None and not c["faltan"]

    sueltas: Dict[int, Dict[str, Any]] = {}
    promos: Dict[Any, Dict[str, Any]] = {}
    grupos_vistos = set()
    extras_sin_costo: Dict[str, int] = {}
    for v in ventas:
        qty = int(v["qty"] or 1)
        pid = int(v["product_id"])
        mv = mods.get(int(v["id"]), [])
        por_item, sin_contar = stock.aplicar_mods(recetas.get(pid, {}), ligados, mv, cuenta=con_costo)
        extras = sum(m[2] for m in mv if m[0] == "add") - sum(m[2] for m in sin_contar)
        for m in sin_contar:
            extras_sin_costo[m[3] or "Extra"] = extras_sin_costo.get(m[3] or "Extra", 0) + qty
        costo_item = _costo(por_item, insumos)
        if v["promo_group"]:
            clave = int(v["promo_id"] or 0) or ("nombre", v["promo_name_snapshot"] or "Promo")
            p = promos.setdefault(clave, {"vendidas": 0, "cobrado": 0.0, "costo": 0.0, "extras": 0, "ok": True,
                                          "nombre": v["promo_name_snapshot"] or "Promo"})
            p["cobrado"] += int(v["promo_price_cents"] or 0) + extras * qty  # el precio viene en un solo componente
            p["extras"] += extras * qty
            p["costo"] += costo_item * qty
            p["ok"] = p["ok"] and ok_producto(pid)
            if (v["order_id"], v["promo_group"]) not in grupos_vistos:
                grupos_vistos.add((v["order_id"], v["promo_group"]))
                p["vendidas"] += 1
        else:
            s = sueltas.setdefault(pid, {"vendidas": 0, "cobrado": 0.0, "costo": 0.0, "extras": 0})
            s["vendidas"] += qty
            s["cobrado"] += (int(v["base"] or 0) + extras) * qty
            s["extras"] += extras * qty
            s["costo"] += costo_item * qty

    def estado_de(pid: int) -> Any:
        c = costos.get(pid)
        if c is None:
            return None, [], "sin_receta"
        return c["costo_cents"], c["faltan"], ("sin_costo" if c["faltan"] else "ok")

    filas: List[Dict[str, Any]] = []
    for p in conn.execute("SELECT id, name, price_cents, active FROM products;").fetchall():
        pid = int(p["id"])
        s = sueltas.get(pid, {"vendidas": 0, "cobrado": 0.0, "costo": 0.0, "extras": 0})
        if not int(p["active"]) and not s["vendidas"]:
            continue
        costo, faltan, estado = estado_de(pid)
        filas.append(_fila(p["name"], int(p["price_cents"] or 0), costo, faltan, estado, s["vendidas"],
                           s["cobrado"], False, s["costo"] if estado == "ok" else None, s["extras"]))

    actuales = {int(p["id"]): p for p in conn.execute("SELECT id, name, price_cents, active FROM promos;").fetchall()}
    comps: Dict[int, List[int]] = {}
    for c in conn.execute("SELECT promo_id, product_id FROM promo_components ORDER BY sort_order, id;").fetchall():
        comps.setdefault(int(c["promo_id"]), []).append(int(c["product_id"]))
    for clave in set(actuales) | set(promos):
        venta = promos.get(clave, {"vendidas": 0, "cobrado": 0.0, "costo": 0.0, "extras": 0, "ok": True,
                                   "nombre": "Promo"})
        actual = actuales.get(clave) if isinstance(clave, int) else None
        if actual is not None and not int(actual["active"]) and not venta["vendidas"]:
            continue
        if actual is not None:
            costo, faltan, estado = (0, [], "ok") if comps.get(clave) else (None, [], "sin_receta")
            for pid in comps.get(clave, []):
                c, f, e = estado_de(pid)
                if e == "sin_receta":
                    costo, estado = None, "sin_receta"
                    break
                costo += c
                faltan += [x for x in f if x not in faltan]
                if e == "sin_costo":
                    estado = "sin_costo"
            precio = int(actual["price_cents"] or 0)
            nombre = actual["name"]
        else:  # una promo que ya no existe: se costea con lo que se vendió
            estado = "ok" if venta["ok"] and venta["vendidas"] else "sin_receta"
            costo = int(round(venta["costo"] / venta["vendidas"])) if estado == "ok" else None
            faltan = []
            precio = round((venta["cobrado"] - venta["extras"]) / venta["vendidas"]) if venta["vendidas"] else 0
            nombre = venta["nombre"]
        real = venta["costo"] if estado == "ok" and venta["ok"] else None
        filas.append(_fila(nombre, precio, costo, faltan, estado, venta["vendidas"], venta["cobrado"], True,
                           real, venta["extras"]))

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
        "extras_sin_costo": [{"nombre": k, "veces": n}
                             for k, n in sorted(extras_sin_costo.items(), key=lambda x: (-x[1], x[0].lower()))],
    }
