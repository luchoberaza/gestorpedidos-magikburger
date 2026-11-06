"""Números del negocio: ventas, gastos y ganancia por período.

Ventas = pedidos (sin el envío, que es del repartidor) + ventas manuales.
Los pedidos se agrupan por jornada; gastos y ventas manuales, por su fecha.
"""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .liquidacion import ESTADOS_ETIQUETA, medios_de_pago, nombre_medio, sql_jornada
from .pedidos import envios_por_pedido


def _rango(desde: Optional[str], hasta: Optional[str]) -> Dict[str, str]:
    return {"desde": desde or "0000-01-01", "hasta": hasta or "9999-12-31"}


def pedidos_del_periodo(conn: sqlite3.Connection, desde: Optional[str], hasta: Optional[str],
                        incluir_abiertos: bool = True) -> List[Dict[str, Any]]:
    r = _rango(desde, hasta)
    j = sql_jornada(conn)
    extra = "" if incluir_abiertos else " AND o.cierre_id IS NOT NULL"
    filas = conn.execute(
        f"SELECT o.id, o.created_at, o.status, o.phone, o.address, o.payment_method, o.total_cents, "
        f"o.cierre_id, o.notes, c.name AS courier_name, {j} AS jornada "
        f"FROM orders o LEFT JOIN couriers c ON c.id=o.courier_id "
        f"WHERE {j} BETWEEN ? AND ?{extra} ORDER BY o.created_at DESC, o.id DESC;",
        (r["desde"], r["hasta"])).fetchall()
    envios = envios_por_pedido(conn, [int(f["id"]) for f in filas])
    medios = medios_de_pago(conn)
    out = []
    for f in filas:
        envio = envios.get(int(f["id"]), 0)
        total = int(f["total_cents"] or 0)
        out.append({
            "id": int(f["id"]), "jornada": f["jornada"], "created_at": f["created_at"],
            "status": f["status"], "status_label": ESTADOS_ETIQUETA.get(f["status"], f["status"]),
            "phone": f["phone"], "address": f["address"], "courier_name": f["courier_name"] or "",
            "payment_method": f["payment_method"], "medio": nombre_medio(medios, f["payment_method"]),
            "total_cents": total, "envio_cents": envio, "neto_cents": total - envio,
            "cerrado": f["cierre_id"] is not None, "notes": f["notes"] or "",
        })
    return out


def ventas_manuales(conn: sqlite3.Connection, desde: Optional[str], hasta: Optional[str]) -> List[Dict[str, Any]]:
    r = _rango(desde, hasta)
    return [dict(x) for x in conn.execute(
        "SELECT * FROM ventas_manuales WHERE fecha BETWEEN ? AND ? ORDER BY fecha DESC, hora DESC, id DESC;",
        (r["desde"], r["hasta"])).fetchall()]


def gastos(conn: sqlite3.Connection, desde: Optional[str], hasta: Optional[str]) -> List[Dict[str, Any]]:
    r = _rango(desde, hasta)
    return [dict(x) for x in conn.execute(
        "SELECT * FROM gastos WHERE fecha BETWEEN ? AND ? ORDER BY fecha DESC, id DESC;",
        (r["desde"], r["hasta"])).fetchall()]


def ranking_productos(conn: sqlite3.Connection, desde: Optional[str], hasta: Optional[str],
                      limite: int = 10) -> List[Dict[str, Any]]:
    r = _rango(desde, hasta)
    j = sql_jornada(conn)
    filas = conn.execute(
        f"SELECT i.order_id, i.qty, COALESCE(i.product_name_snapshot, i.name_snapshot) AS nombre, "
        f"COALESCE(NULLIF(i.product_price_cents_snapshot, 0), i.base_price_cents) AS base, "
        f"i.promo_group, i.promo_name_snapshot, i.promo_price_cents, "
        f"(SELECT COALESCE(SUM(m.price_cents), 0) FROM order_item_mods m "
        f" WHERE m.order_item_id = i.id AND m.kind = 'add') AS extras "
        f"FROM order_items i JOIN orders o ON o.id = i.order_id WHERE {j} BETWEEN ? AND ?;",
        (r["desde"], r["hasta"])).fetchall()
    acum: Dict[str, Dict[str, Any]] = {}
    promos_vistas = set()
    for f in filas:
        qty = int(f["qty"] or 1)
        if f["promo_group"]:
            nombre = "Promo · " + (f["promo_name_snapshot"] or "Promo")
            d = acum.setdefault(nombre, {"nombre": nombre, "unidades": 0, "total_cents": 0, "promo": True})
            clave = (f["order_id"], f["promo_group"])
            if clave not in promos_vistas:
                promos_vistas.add(clave)
                d["unidades"] += 1
            d["total_cents"] += int(f["promo_price_cents"] or 0) + int(f["extras"] or 0) * qty
        else:
            nombre = f["nombre"] or "Producto"
            d = acum.setdefault(nombre, {"nombre": nombre, "unidades": 0, "total_cents": 0, "promo": False})
            d["unidades"] += qty
            d["total_cents"] += (int(f["base"] or 0) + int(f["extras"] or 0)) * qty
    # Ventas manuales también suman al ranking por su concepto.
    for v in conn.execute("SELECT concepto, cantidad, total_cents FROM ventas_manuales WHERE fecha BETWEEN ? AND ?;",
                          (r["desde"], r["hasta"])).fetchall():
        nombre = (v["concepto"] or "Venta manual").strip()
        d = acum.setdefault(nombre, {"nombre": nombre, "unidades": 0, "total_cents": 0, "promo": False})
        d["unidades"] += float(v["cantidad"] or 0)
        d["total_cents"] += int(v["total_cents"] or 0)
    lista = sorted(acum.values(), key=lambda d: (-d["total_cents"], -d["unidades"], d["nombre"]))
    for d in lista:
        u = d["unidades"]
        d["unidades"] = int(u) if float(u).is_integer() else round(u, 2)
    return lista[:limite] if limite else lista


def _dias(desde: str, hasta: str) -> List[str]:
    d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
    if (d1 - d0).days > 400:
        d0 = d1 - timedelta(days=400)
    out, d = [], d0
    while d <= d1:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def resumen(conn: sqlite3.Connection, desde: str, hasta: str, detalle: bool = True) -> Dict[str, Any]:
    pedidos = pedidos_del_periodo(conn, desde, hasta)
    manuales = ventas_manuales(conn, desde, hasta)
    gs = gastos(conn, desde, hasta)

    ventas_pedidos = sum(p["neto_cents"] for p in pedidos)
    envios = sum(p["envio_cents"] for p in pedidos)
    ventas_man = sum(int(v["total_cents"] or 0) for v in manuales)
    total_gastos = sum(int(g["importe_cents"] or 0) for g in gs)
    ventas = ventas_pedidos + ventas_man

    out: Dict[str, Any] = {
        "desde": desde, "hasta": hasta,
        "pedidos_count": len(pedidos),
        "ventas_pedidos_cents": ventas_pedidos,
        "ventas_manuales_cents": ventas_man,
        "ventas_cents": ventas,
        "envios_cents": envios,
        "gastos_cents": total_gastos,
        "balance_cents": ventas - total_gastos,
        "ticket_promedio_cents": int(round(ventas_pedidos / len(pedidos))) if pedidos else 0,
        "gastos_count": len(gs),
        "manuales_count": len(manuales),
    }
    if not detalle:
        return out

    por_dia: Dict[str, Dict[str, int]] = {}
    for p in pedidos:
        d = por_dia.setdefault(p["jornada"], {"ventas": 0, "gastos": 0, "pedidos": 0})
        d["ventas"] += p["neto_cents"]
        d["pedidos"] += 1
    for v in manuales:
        por_dia.setdefault(v["fecha"], {"ventas": 0, "gastos": 0, "pedidos": 0})["ventas"] += int(v["total_cents"] or 0)
    for g in gs:
        por_dia.setdefault(g["fecha"], {"ventas": 0, "gastos": 0, "pedidos": 0})["gastos"] += int(g["importe_cents"] or 0)

    por_cat: Dict[str, int] = {}
    for g in gs:
        k = g["categoria"] or "Sin categoría"
        por_cat[k] = por_cat.get(k, 0) + int(g["importe_cents"] or 0)

    por_medio: Dict[str, int] = {}
    for p in pedidos:
        por_medio[p["medio"]] = por_medio.get(p["medio"], 0) + p["neto_cents"]
    for v in manuales:
        k = v["metodo_pago"] or "Sin especificar"
        por_medio[k] = por_medio.get(k, 0) + int(v["total_cents"] or 0)

    por_rep: Dict[str, Dict[str, int]] = {}
    for p in pedidos:
        k = p["courier_name"] or "Sin repartidor"
        d = por_rep.setdefault(k, {"pedidos": 0, "envios_cents": 0, "ventas_cents": 0})
        d["pedidos"] += 1
        d["envios_cents"] += p["envio_cents"]
        d["ventas_cents"] += p["neto_cents"]

    por_hora: Dict[int, int] = {}
    for p in pedidos:
        try:
            h = int(str(p["created_at"]).replace("T", " ")[11:13])
        except ValueError:
            continue
        por_hora[h] = por_hora.get(h, 0) + 1

    dias = _dias(desde, hasta) if desde > "0000-01-01" and hasta < "9999-12-31" else sorted(por_dia)
    out.update({
        "serie_dias": [{"dia": d, **por_dia.get(d, {"ventas": 0, "gastos": 0, "pedidos": 0})} for d in dias],
        "gastos_por_categoria": sorted(({"categoria": k, "total_cents": v} for k, v in por_cat.items()),
                                       key=lambda x: -x["total_cents"]),
        "ventas_por_medio": sorted(({"medio": k, "total_cents": v} for k, v in por_medio.items()),
                                   key=lambda x: -x["total_cents"]),
        "por_repartidor": sorted(({"nombre": k, **v} for k, v in por_rep.items()), key=lambda x: -x["pedidos"]),
        "pedidos_por_hora": [{"hora": h, "pedidos": por_hora.get(h, 0)} for h in range(24)],
        "top_productos": ranking_productos(conn, desde, hasta, 10),
    })
    return out


def meses_atras(hoy: str, n: int) -> List[Dict[str, str]]:
    d = date.fromisoformat(hoy).replace(day=1)
    out = []
    for _ in range(n):
        fin = (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        out.append({"desde": d.isoformat(), "hasta": fin.isoformat(), "etiqueta": d.strftime("%m/%y")})
        d = (d - timedelta(days=1)).replace(day=1)
    return list(reversed(out))


def comparacion_meses(conn: sqlite3.Connection, hoy: str, n: int = 6) -> List[Dict[str, Any]]:
    out = []
    for m in meses_atras(hoy, n):
        r = resumen(conn, m["desde"], m["hasta"], detalle=False)
        out.append({"mes": m["etiqueta"], "ventas_cents": r["ventas_cents"], "gastos_cents": r["gastos_cents"],
                    "balance_cents": r["balance_cents"]})
    return out


def _vencimiento(anio: int, mes: int, dia: int) -> date:
    ultimo = calendar.monthrange(anio, mes)[1]
    return date(anio, mes, max(1, min(int(dia or 1), ultimo)))


def recordatorios_proximos(conn: sqlite3.Connection, hoy: str, dias_aviso: int) -> List[Dict[str, Any]]:
    """Pagos fijos vencidos este mes sin marcar como pagados, o que vencen dentro
    de `dias_aviso` días (incluido el vencimiento del mes que viene)."""
    d = date.fromisoformat(hoy)
    mes = d.strftime("%Y-%m")
    sig = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    out = []
    for r in conn.execute("SELECT * FROM recordatorios WHERE activo=1;").fetchall():
        pagado_hasta = r["ultimo_pago"] or ""
        candidatos = []
        if pagado_hasta < mes:
            candidatos.append((_vencimiento(d.year, d.month, r["dia"]), mes))
        if pagado_hasta < sig.strftime("%Y-%m"):
            candidatos.append((_vencimiento(sig.year, sig.month, r["dia"]), sig.strftime("%Y-%m")))
        for venc, periodo in candidatos:
            diff = (venc - d).days
            if diff < 0 or diff <= dias_aviso:
                out.append({**dict(r), "vence": venc.isoformat(), "periodo": periodo,
                            "dias_restantes": diff, "vencido": diff < 0})
                break
    return sorted(out, key=lambda x: x["dias_restantes"])
