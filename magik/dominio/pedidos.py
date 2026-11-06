"""Reglas de los pedidos: ítems, promos, ajustes y total.

El total lo calcula SIEMPRE el servidor con los precios de la base; la pantalla
manda el que le mostró al cajero y, si no coinciden, el pedido no se guarda.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from ..db import tiene_columna


class DatosDelPedidoInvalidos(Exception):
    """Lo que manda la pantalla no coincide con el catálogo: no se guarda a medias."""


def money(cents: Any) -> int:
    return int(round((int(cents or 0)) / 100))


def pesos_a_centavos(valor: Any, defecto: int = 0, negativo: bool = False) -> int:
    """'1500', '1.500', '1.500,50', '-200' → centavos. Inválido → defecto."""
    if valor is None:
        return defecto
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        cents = int(round(float(valor) * 100))
    else:
        s = str(valor).strip().replace("$", "").replace(" ", "")
        if not s:
            return defecto
        neg = s.startswith("-")
        s = s.lstrip("+-")
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        elif s.count(".") > 1 or (s.count(".") == 1 and len(s.split(".")[1]) == 3):
            s = s.replace(".", "")  # 1.500 = mil quinientos
        try:
            cents = int(round(float(s) * 100))
        except ValueError:
            return defecto
        if neg:
            cents = -cents
    if not negativo and cents < 0:
        cents = abs(cents)
    return cents


def es_rotulo_de_envio(label: Any) -> bool:
    """Criterio VIEJO (por texto). Solo para clasificar ajustes cargados antes de
    que existiera `kind`: el cajero podía escribir 'Delivery' o 'Flete'."""
    s = (str(label or "")).strip().lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ü", "u")):
        s = s.replace(a, b)
    return s.startswith("envio")


def ajuste_es_envio(fila: Any) -> bool:
    try:
        kind = fila["kind"]
    except (IndexError, KeyError):
        kind = None
    if kind:
        return str(kind).strip().lower() == "delivery"
    return es_rotulo_de_envio(fila["label"])


def envios_por_pedido(conn: sqlite3.Connection, order_ids: Optional[List[int]] = None) -> Dict[int, int]:
    """Envío cobrado en cada pedido (solo positivos: un envío negativo es bonificación)."""
    salida: Dict[int, int] = {}
    campos = "order_id, label, amount_cents" + (
        ", kind" if tiene_columna(conn, "order_adjustments", "kind") else "")
    sql = f"SELECT {campos} FROM order_adjustments"
    params: List[Any] = []
    if order_ids is not None:
        if not order_ids:
            return salida
        sql += f" WHERE order_id IN ({','.join('?' * len(order_ids))})"
        params = list(order_ids)
    for r in conn.execute(sql, params).fetchall():
        amt = int(r["amount_cents"] or 0)
        if amt > 0 and ajuste_es_envio(r):
            oid = int(r["order_id"])
            salida[oid] = salida.get(oid, 0) + amt
    return salida


def verificar_total(total_servidor: int, total_pantalla: Any) -> None:
    """Si el dueño cambió un precio mientras la caja tenía la pantalla abierta,
    lo cobrado y lo grabado no coincidirían: mejor no guardar."""
    if total_pantalla is None:
        return
    try:
        esperado = int(total_pantalla)
    except (TypeError, ValueError):
        return
    if esperado != total_servidor:
        raise DatosDelPedidoInvalidos(
            f"Los precios cambiaron mientras cargabas el pedido: la pantalla dice "
            f"${money(esperado)} y el sistema calcula ${money(total_servidor)}. "
            f"Ya actualicé los precios: revisá el pedido y volvé a confirmarlo."
        )


def insertar_ajustes(cur: sqlite3.Cursor, order_id: int, ajustes: Any) -> int:
    """Envío, descuentos, recargos. Devuelve la suma (puede ser negativa)."""
    if not isinstance(ajustes, list):
        return 0
    total, orden = 0, 0
    for adj in ajustes:
        if not isinstance(adj, dict):
            continue
        label = (str(adj.get("label") or "").strip())[:60] or "Ajuste"
        try:
            amount = int(adj.get("amount_cents") or 0)
        except (TypeError, ValueError):
            amount = 0
        if amount == 0:
            continue
        kind = (str(adj.get("kind") or "").strip().lower())[:20]
        if not kind and es_rotulo_de_envio(label):
            kind = "delivery"
        cur.execute(
            "INSERT INTO order_adjustments(order_id, label, amount_cents, sort_order, kind) "
            "VALUES(?,?,?,?,?);",
            (order_id, label, amount, orden, kind),
        )
        orden += 1
        total += amount
    return total


def insertar_items(conn: sqlite3.Connection, cur: sqlite3.Cursor, order_id: int, items: Any) -> int:
    """Inserta ítems y modificaciones. Devuelve el total en centavos.

    Las promos cobran su precio fijo una vez por grupo (el de la tabla promos);
    sus componentes no suman precio base, solo sus extras.
    """
    productos = {int(p["id"]): p for p in conn.execute(
        "SELECT id, name, price_cents FROM products;").fetchall()}
    ingredientes = {int(i["id"]): i for i in conn.execute(
        "SELECT id, name, extra_price_cents, active FROM ingredients;").fetchall()}
    promos = {int(p["id"]): p for p in conn.execute(
        "SELECT id, name, price_cents FROM promos;").fetchall()}

    total, promo_visto = 0, set()
    if not isinstance(items, list) or not items:
        raise DatosDelPedidoInvalidos("El pedido está vacío.")

    for it in items:
        if not isinstance(it, dict):
            raise DatosDelPedidoInvalidos("Un ítem del pedido llegó mal. Volvé a cargarlo.")
        try:
            pid = int(it.get("product_id") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid not in productos:
            raise DatosDelPedidoInvalidos(
                "Uno de los productos del pedido ya no existe en el catálogo. "
                "Actualicé la lista: revisá el pedido y volvé a confirmarlo.")
        try:
            qty = max(1, min(int(it.get("qty") or 1), 99))
        except (TypeError, ValueError):
            qty = 1
        p = productos[pid]

        grupo = (str(it.get("promo_group") or "").strip())[:40] or None
        precio_promo_guardado = 0
        promo_id, promo_nombre = 0, None
        if grupo:
            try:
                promo_id = int(it.get("promo_id") or 0)
            except (TypeError, ValueError):
                promo_id = 0
            promo = promos.get(promo_id)
            promo_nombre = promo["name"] if promo else (str(it.get("promo_name") or "Promo")[:80])
            precio = int(promo["price_cents"]) if promo else int(it.get("promo_price_cents") or 0)
            if grupo not in promo_visto:
                precio_promo_guardado = precio
                promo_visto.add(grupo)

        base = 0 if grupo else int(p["price_cents"])
        cur.execute(
            "INSERT INTO order_items(order_id, product_id, qty, name_snapshot, product_name_snapshot, "
            "base_price_cents, product_price_cents_snapshot, promo_group, promo_name_snapshot, "
            "promo_price_cents, promo_id) VALUES(?,?,?,?,?,?,?,?,?,?,?);",
            (order_id, pid, qty, p["name"], p["name"], base, base, grupo, promo_nombre,
             precio_promo_guardado, promo_id),
        )
        item_id = int(cur.lastrowid)

        for rid in it.get("removed_ingredient_ids") or []:
            try:
                rid = int(rid)
            except (TypeError, ValueError):
                continue
            if rid in ingredientes:
                cur.execute(
                    "INSERT INTO order_item_mods(order_item_id, kind, ingredient_id, name_snapshot, price_cents) "
                    "VALUES(?, 'remove', ?, ?, 0);", (item_id, rid, ingredientes[rid]["name"]))

        extras = 0
        for aid in it.get("added_ingredient_ids") or []:
            try:
                aid = int(aid)
            except (TypeError, ValueError):
                continue
            ing = ingredientes.get(aid)
            if ing is None:
                continue
            precio = int(ing["extra_price_cents"] or 0)
            extras += precio
            cur.execute(
                "INSERT INTO order_item_mods(order_item_id, kind, ingredient_id, name_snapshot, price_cents) "
                "VALUES(?, 'add', ?, ?, ?);", (item_id, aid, ing["name"], precio))

        total += (base + extras) * qty + precio_promo_guardado
    return total


def borrar_contenido(cur: sqlite3.Cursor, order_id: int) -> None:
    cur.execute("DELETE FROM order_item_mods WHERE order_item_id IN "
                "(SELECT id FROM order_items WHERE order_id=?);", (order_id,))
    cur.execute("DELETE FROM order_items WHERE order_id=?;", (order_id,))
    cur.execute("DELETE FROM order_adjustments WHERE order_id=?;", (order_id,))


def leer_pedido(conn: sqlite3.Connection, order_id: int) -> Optional[Dict[str, Any]]:
    """Pedido completo con ítems, modificaciones y ajustes (para editar)."""
    o = conn.execute(
        "SELECT o.*, c.name AS courier_name FROM orders o "
        "LEFT JOIN couriers c ON c.id=o.courier_id WHERE o.id=?;", (order_id,)).fetchone()
    if not o:
        return None
    items = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id;", (order_id,)).fetchall()
    mods = conn.execute(
        "SELECT order_item_id, kind, ingredient_id FROM order_item_mods WHERE order_item_id IN "
        "(SELECT id FROM order_items WHERE order_id=?) ORDER BY id;", (order_id,)).fetchall()
    por_item: Dict[int, Dict[str, List[int]]] = {}
    for m in mods:
        d = por_item.setdefault(int(m["order_item_id"]), {"remove": [], "add": []})
        k = (m["kind"] or "").strip()
        if k in d:
            d[k].append(int(m["ingredient_id"] or 0))
    adjs = conn.execute(
        "SELECT label, amount_cents, kind FROM order_adjustments WHERE order_id=? "
        "ORDER BY sort_order, id;", (order_id,)).fetchall()
    return {
        "order": {
            "id": int(o["id"]), "phone": o["phone"], "address": o["address"],
            "courier_id": o["courier_id"], "payment_method": o["payment_method"] or "cash",
            "status": o["status"], "notes": o["notes"] if "notes" in o.keys() else "",
            "created_at": o["created_at"], "total_cents": int(o["total_cents"] or 0),
            "cierre_id": o["cierre_id"] if "cierre_id" in o.keys() else None,
            "courier_name": o["courier_name"],
        },
        "items": [{
            "id": int(it["id"]),
            "product_id": int(it["product_id"]),
            "name": it["product_name_snapshot"] or it["name_snapshot"],
            "qty": int(it["qty"] or 1),
            "removed_ingredient_ids": por_item.get(int(it["id"]), {}).get("remove", []),
            "added_ingredient_ids": por_item.get(int(it["id"]), {}).get("add", []),
            "promo_group": it["promo_group"] or None,
            "promo_id": int(it["promo_id"] or 0),
            "promo_name": it["promo_name_snapshot"] or "",
            "promo_price_cents": int(it["promo_price_cents"] or 0),
        } for it in items],
        "adjustments": [{"label": a["label"], "amount_cents": int(a["amount_cents"] or 0),
                         "kind": a["kind"] or ""} for a in adjs],
    }
