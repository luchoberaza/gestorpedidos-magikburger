"""Operación del día: tomar pedidos, tablero, ticket, liquidación y cierre."""
from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List

from flask import Blueprint, abort, jsonify, render_template, request, send_file

from .. import auth, config
from ..db import ahora_iso, get_db, respaldo_automatico
from ..documentos import guardar_reporte, pdf_cierre, ruta_reporte
from ..dominio import liquidacion as liq
from ..dominio import stock
from ..dominio.pedidos import (DatosDelPedidoInvalidos, PagaConInvalido, borrar_contenido, insertar_ajustes,
                               insertar_items, leer_pedido, money, paga_con, verificar_total)
from ..ticket import bytes_escpos, contexto, imprimir
from .util import bandera, cuerpo, error, ok, texto

bp = Blueprint("operacion", __name__)


# ---------------------------------------------------------------------------
# Catálogo para la caja
# ---------------------------------------------------------------------------
@bp.get("/api/bootstrap")
@auth.requiere("pos", "pedidos")
def api_bootstrap():
    conn = get_db()
    base: Dict[int, List[int]] = {}
    for r in conn.execute("SELECT product_id, ingredient_id FROM product_base_ingredients;"):
        base.setdefault(int(r["product_id"]), []).append(int(r["ingredient_id"]))
    comp: Dict[int, List[int]] = {}
    for r in conn.execute("SELECT promo_id, product_id FROM promo_components ORDER BY sort_order, id;"):
        comp.setdefault(int(r["promo_id"]), []).append(int(r["product_id"]))
    cfg = config.todas(conn)
    return ok(
        products=[{"id": int(p["id"]), "name": p["name"], "price_cents": int(p["price_cents"] or 0),
                   "active": int(p["active"] or 0), "category": p["category"] or "",
                   "base_ingredient_ids": base.get(int(p["id"]), [])}
                  for p in conn.execute("SELECT * FROM products ORDER BY sort_order, name COLLATE NOCASE;")],
        ingredients=[{"id": int(i["id"]), "name": i["name"], "extra_price_cents": int(i["extra_price_cents"] or 0),
                      "active": int(i["active"] or 0)}
                     for i in conn.execute("SELECT * FROM ingredients ORDER BY active DESC, name COLLATE NOCASE;")],
        couriers=[{"id": int(c["id"]), "name": c["name"], "active": int(c["active"] or 0)}
                  for c in conn.execute("SELECT * FROM couriers ORDER BY name COLLATE NOCASE;")],
        promos=[{"id": int(p["id"]), "name": p["name"], "price_cents": int(p["price_cents"] or 0),
                 "component_product_ids": comp.get(int(p["id"]), [])}
                for p in conn.execute("SELECT * FROM promos WHERE active=1 ORDER BY name COLLATE NOCASE;")],
        metodos_pago=[{"code": m["code"], "name": m["name"], "is_cash": int(m["is_cash"]),
                       "active": int(m["active"])}
                      for m in conn.execute("SELECT * FROM metodos_pago ORDER BY sort_order, id;")],
        settings={"ticket_copies": cfg["ticket_copies"], "delivery_fee_cents": cfg["delivery_fee_cents"]},
    )


@bp.get("/api/clientes")
@auth.requiere("pos", "pedidos")
def api_clientes():
    """Direcciones que ya usó un teléfono (para autocompletar en la caja)."""
    tel = "".join(ch for ch in str(request.args.get("tel") or "") if ch.isalnum())
    if len(tel) < 6:
        return ok(direcciones=[], pedidos=0)
    conn = get_db()
    filas = conn.execute(
        "SELECT address, MAX(created_at) AS ultima, COUNT(*) AS n FROM orders "
        "WHERE replace(replace(replace(phone,' ',''),'-',''),'.','') = ? "
        "GROUP BY address ORDER BY ultima DESC LIMIT 4;", (tel,)).fetchall()
    return ok(direcciones=[f["address"] for f in filas], pedidos=sum(int(f["n"]) for f in filas))


# ---------------------------------------------------------------------------
# Tablero (pedidos abiertos)
# ---------------------------------------------------------------------------
@bp.get("/api/board")
@auth.requiere("pedidos", "pos")
def api_board():
    conn = get_db()
    hoy = liq.jornada_actual(conn)
    medios = liq.medios_de_pago(conn)
    filas = conn.execute(
        f"SELECT o.*, c.name AS courier_name, {liq.sql_jornada(conn)} AS jornada, "
        f"(SELECT COALESCE(SUM(qty),0) FROM order_items i WHERE i.order_id=o.id) AS unidades "
        f"FROM orders o LEFT JOIN couriers c ON c.id=o.courier_id WHERE o.cierre_id IS NULL "
        f"ORDER BY o.id DESC;").fetchall()
    out: Dict[str, List[Any]] = {k: [] for k in liq.ESTADOS}
    for r in filas:
        st = (r["status"] or "new").strip()
        if st not in out:
            st = "new"
        out[st].append({
            "id": int(r["id"]), "created_at": r["created_at"], "phone": r["phone"], "address": r["address"],
            "courier_id": r["courier_id"], "courier_name": r["courier_name"],
            "total_cents": int(r["total_cents"] or 0), "status": st,
            "payment_method": r["payment_method"], "medio": liq.nombre_medio(medios, r["payment_method"]),
            "es_efectivo": liq.es_efectivo(medios, r["payment_method"]),
            "notes": r["notes"] or "", "unidades": int(r["unidades"] or 0),
            "paga_con_cents": r["paga_con_cents"],
            "jornada": r["jornada"], "anterior": (r["jornada"] or hoy) < hoy,
        })
    return ok(hoy=hoy, **out)


# ---------------------------------------------------------------------------
# Pedidos
# ---------------------------------------------------------------------------
def _medio_valido(conn: sqlite3.Connection, raw: Any) -> str:
    code = str(raw or "cash").strip()
    fila = conn.execute("SELECT code FROM metodos_pago WHERE code=?;", (code,)).fetchone()
    if fila:
        return fila["code"]
    s = code.lower()
    return "transfer" if (s.startswith("transf") or "transfer" in s) else "cash"


def _validar_basicos(d: Dict[str, Any]) -> Any:
    if not texto(d, "phone") or not texto(d, "address"):
        return "Faltan datos: dirección y teléfono."
    items = d.get("items")
    if not isinstance(items, list) or not items:
        return "El pedido está vacío: agregá al menos un producto."
    return None


def _paga_con(conn: sqlite3.Connection, d: Dict[str, Any], total: int) -> Any:
    """Con cuánto paga en efectivo (para el vuelto de la factura). Solo si el medio es efectivo."""
    medio = _medio_valido(conn, d.get("payment_method"))
    return paga_con(d.get("paga_con_cents"), total, liq.es_efectivo(liq.medios_de_pago(conn), medio))


def _courier(conn: sqlite3.Connection, raw: Any) -> Any:
    try:
        cid = int(raw) if raw not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return None
    if cid is not None and not conn.execute("SELECT 1 FROM couriers WHERE id=?;", (cid,)).fetchone():
        raise DatosDelPedidoInvalidos("El repartidor elegido ya no existe. Elegí otro.")
    return cid


@bp.post("/api/orders")
@auth.requiere("pos")
def api_crear_pedido():
    d = cuerpo()
    falta = _validar_basicos(d)
    if falta:
        return error(falta)
    conn = get_db()
    cur = conn.cursor()
    try:
        u = auth.usuario_actual()
        cur.execute(
            "INSERT INTO orders(created_at, status, phone, address, courier_id, payment_method, total_cents, notes, "
            "created_by) VALUES(?, 'new', ?, ?, ?, ?, 0, ?, ?);",
            (ahora_iso(), texto(d, "phone", 40), texto(d, "address", 160), _courier(conn, d.get("courier_id")),
             _medio_valido(conn, d.get("payment_method")), texto(d, "notes", 200), u["id"] if u else None))
        oid = int(cur.lastrowid)
        total = insertar_items(conn, cur, oid, d.get("items"))
        total += insertar_ajustes(cur, oid, d.get("adjustments") or [])
        verificar_total(total, d.get("client_total_cents"))
        cur.execute("UPDATE orders SET total_cents=?, paga_con_cents=? WHERE id=?;",
                    (total, _paga_con(conn, d, total), oid))
        conn.commit()
        return ok(id=oid, total_cents=total)
    except DatosDelPedidoInvalidos as e:
        conn.rollback()
        return error(str(e), 409, stale=True)
    except PagaConInvalido as e:
        conn.rollback()
        return error(str(e))
    except sqlite3.Error as e:
        conn.rollback()
        return error(f"No se pudo guardar el pedido (base de datos: {e}).", 500)


@bp.get("/api/orders/<int:order_id>")
@auth.requiere("pedidos", "pos")
def api_ver_pedido(order_id: int):
    p = leer_pedido(get_db(), order_id)
    if p is None:
        return error("Ese pedido no existe (quizás lo eliminaron).", 404)
    return ok(**p)


def _abierto(conn: sqlite3.Connection, order_id: int) -> Any:
    fila = conn.execute("SELECT id, status, cierre_id, total_cents, address FROM orders WHERE id=?;",
                        (order_id,)).fetchone()
    if fila is None:
        return None, error("Ese pedido no existe (quizás lo eliminaron).", 404)
    if fila["cierre_id"] is not None:
        return None, error(f"Este pedido ya está cerrado (cierre #{fila['cierre_id']}): forma parte del historial "
                           f"y no se puede modificar.", 409)
    return fila, None


@bp.post("/api/orders/<int:order_id>/update")
@auth.requiere("pedidos")
def api_editar_pedido(order_id: int):
    d = cuerpo()
    falta = _validar_basicos(d)
    if falta:
        return error(falta)
    conn = get_db()
    fila, err = _abierto(conn, order_id)
    if err:
        return err
    if (fila["status"] or "") in liq.ENTREGADOS and not bandera(d, "confirm_delivered"):
        return error("Este pedido ya figura como entregado. Si lo modificás cambia el efectivo a rendir de ese "
                     "repartidor. ¿Seguro?", 409, needs_confirm=True)
    cur = conn.cursor()
    try:
        cur.execute("UPDATE orders SET phone=?, address=?, courier_id=?, payment_method=?, notes=? WHERE id=?;",
                    (texto(d, "phone", 40), texto(d, "address", 160), _courier(conn, d.get("courier_id")),
                     _medio_valido(conn, d.get("payment_method")), texto(d, "notes", 200), order_id))
        borrar_contenido(cur, order_id)
        total = insertar_items(conn, cur, order_id, d.get("items"))
        total += insertar_ajustes(cur, order_id, d.get("adjustments") or [])
        verificar_total(total, d.get("client_total_cents"))
        cur.execute("UPDATE orders SET total_cents=?, paga_con_cents=? WHERE id=?;",
                    (total, _paga_con(conn, d, total), order_id))
        if total != int(fila["total_cents"] or 0):
            auth.auditar(conn, "Pedido editado",
                         f"#{order_id} {fila['address']}: ${money(fila['total_cents'])} → ${money(total)}")
        conn.commit()
        return ok(total_cents=total)
    except DatosDelPedidoInvalidos as e:
        conn.rollback()
        return error(str(e), 409, stale=True)
    except PagaConInvalido as e:
        conn.rollback()
        return error(str(e))
    except sqlite3.Error as e:
        conn.rollback()
        return error(f"No se pudo guardar el pedido (base de datos: {e}).", 500)


@bp.post("/api/orders/<int:order_id>/status")
@auth.requiere("pedidos")
def api_estado(order_id: int):
    estado = texto(cuerpo(), "status", 20)
    if estado not in liq.ESTADOS:
        return error("Estado inválido.")
    conn = get_db()
    _, err = _abierto(conn, order_id)
    if err:
        return err
    conn.execute("UPDATE orders SET status=? WHERE id=?;", (estado, order_id))
    conn.commit()
    return ok()


@bp.post("/api/orders/<int:order_id>/delete")
@auth.requiere("pedidos_eliminar")
def api_eliminar(order_id: int):
    conn = get_db()
    fila, err = _abierto(conn, order_id)
    if err:
        return err
    cur = conn.cursor()
    borrar_contenido(cur, order_id)
    cur.execute("DELETE FROM orders WHERE id=?;", (order_id,))
    auth.auditar(conn, "Pedido eliminado", f"#{order_id} {fila['address']} · ${money(fila['total_cents'])}")
    conn.commit()
    return ok()


# ---------------------------------------------------------------------------
# Ticket e impresión
# ---------------------------------------------------------------------------
def _copias() -> Any:
    try:
        c = int(request.args.get("copies") or 0)
        return c if c > 0 else None
    except ValueError:
        return None


@bp.get("/orders/<int:order_id>/ticket")
@auth.requiere("pos", "pedidos")
def ver_ticket(order_id: int):
    ctx = contexto(get_db(), order_id, _copias())
    if ctx is None:
        abort(404)
    return render_template("ticket.html", order=ctx["order"], blocks=ctx["blocks"], adjustments=ctx["adjustments"],
                           copies=ctx["copies"], negocio=ctx["negocio"],
                           autoprint=request.args.get("autoprint") in ("1", "true", "yes"))


@bp.post("/orders/<int:order_id>/print_escpos")
@auth.requiere("pos", "pedidos")
def imprimir_ticket(order_id: int):
    """Imprime por ESC/POS directo. Nunca da 500: si falla, la pantalla usa la vista HTML."""
    conn = get_db()
    try:
        ctx = contexto(conn, order_id, _copias())
        if ctx is None:
            return error("Ese pedido no existe.", 404)
        datos, modo = bytes_escpos(ctx)
        impresora = imprimir(conn, datos)
        return ok(printer=impresora, modo=modo)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Liquidación y cierre
# ---------------------------------------------------------------------------
@bp.get("/api/liquidation")
@auth.requiere("liquidacion")
def api_liquidacion():
    conn = get_db()
    hoy = liq.jornada_actual(conn)
    r = liq.calcular(conn, hoy)
    carry = liq.arrastrados(conn, hoy)
    abiertos = conn.execute("SELECT COUNT(*) FROM orders WHERE cierre_id IS NULL;").fetchone()[0]
    return ok(
        day=hoy, jornada=hoy,
        total_cents=r["gross_total_cents"], transfer_cents=r["transfer_cents"],
        cash_collected_cents=r["cash_cents"], cash_base_cents=r["cash_cents"] - r["delivery_cents"],
        cash_float_total_cents=r["cash_float_total_cents"], cash_to_render_cents=r["cash_to_render_cents"],
        delivery_total_cents=r["delivery_all_cents"], delivery_cash_total_cents=r["delivery_cents"],
        pending_cents=r["pending_cents"], pending_count=r["pending_count"],
        orders_count=r["orders_count"], delivered_count=r["delivered_count"],
        carryover_count=carry["count"], carryover_cents=carry["total_cents"],
        abiertos_total=int(abiertos), por_medio=r["por_medio"],
        couriers=[{
            "courier_id": c["courier_id"], "courier_name": c["courier_name"],
            "orders_count": c["orders_count"], "delivered_count": c["delivered_count"],
            "pending_count": c["pending_count"], "pending_cents": c["pending_cents"],
            "transfer_cents": c["transfer_cents"], "cash_float_cents": c["cash_float_cents"],
            "delivery_cents": c["delivery_cents"], "delivery_all_cents": c["delivery_all_cents"],
            "gross_total_cents": c["gross_total_cents"], "cash_collected_cents": c["cash_cents"],
            # cash_cents / total_cents = efectivo a rendir (lo que la pantalla muestra en grande)
            "cash_cents": c["to_render_cents"], "total_cents": c["to_render_cents"],
        } for c in r["couriers"]],
    )


@bp.post("/api/cierre")
@auth.requiere("cierre")
def api_cierre():
    """Cierra la jornada: archiva el PDF, pasa los pedidos abiertos al historial,
    descuenta el stock por receta y deja un respaldo. Nada se borra."""
    d = cuerpo()
    conn = get_db()
    abiertos = conn.execute("SELECT COUNT(*) FROM orders WHERE cierre_id IS NULL;").fetchone()[0]
    if not abiertos:
        return error("No hay pedidos abiertos para cerrar.")
    hoy = liq.jornada_actual(conn)
    incluir = bandera(d, "include_adjustments", True)
    try:
        pdf = pdf_cierre(conn, hoy, incluir)
    except Exception as e:
        return error(f"No se pudo generar el PDF del cierre, así que no se cerró nada. Detalle: {e}", 500)
    archivo = guardar_reporte(f"Cierre_{hoy}.pdf", pdf)
    if archivo is None:
        return error("No se pudo guardar el PDF del cierre en el disco, así que no se cerró nada. "
                     "Revisá el espacio libre y volvé a intentar.", 500)
    u = auth.usuario_actual()
    try:
        r = liq.calcular(conn, hoy)
        todos = liq.calcular(conn, None)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cierres(jornada, created_at, user_id, user_name, orders_count, gross_total_cents, "
            "cash_to_render_cents, other_methods_cents, delivery_cents, pdf_filename, resumen_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?);",
            (hoy, ahora_iso(), u["id"] if u else None, u["name"] if u else "", todos["orders_count"],
             todos["gross_total_cents"], r["cash_to_render_cents"], r["transfer_cents"], r["delivery_all_cents"],
             archivo, json.dumps({"jornada": r, "incluye_ajustes": incluir}, default=str)))
        cierre_id = int(cur.lastrowid)
        cur.execute("UPDATE orders SET cierre_id=? WHERE cierre_id IS NULL;", (cierre_id,))
        movidos = stock.descontar_cierre(conn, cierre_id, u)
        auth.auditar(conn, "Cierre de jornada",
                     f"#{cierre_id} {hoy}: {todos['orders_count']} pedidos, a rendir ${money(r['cash_to_render_cents'])}")
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        return error(f"No se pudo cerrar la jornada (base de datos: {e}). No se cambió nada.", 500)
    if config.obtener(conn, "respaldo_auto"):
        respaldo_automatico("cierre", config.obtener(conn, "respaldos_a_conservar"))
    return ok(cierre_id=cierre_id, archivo=archivo, pdf_url=f"/api/cierres/{cierre_id}/pdf",
              pedidos=todos["orders_count"], insumos_descontados=movidos)


@bp.get("/api/cierres")
@auth.requiere("liquidacion", "reportes")
def api_cierres():
    filas = get_db().execute("SELECT id, jornada, created_at, user_name, orders_count, gross_total_cents, "
                             "cash_to_render_cents, other_methods_cents, delivery_cents, pdf_filename "
                             "FROM cierres ORDER BY id DESC LIMIT 200;").fetchall()
    return ok(cierres=[dict(f) for f in filas])


@bp.get("/api/cierres/<int:cierre_id>/pdf")
@auth.requiere("liquidacion", "reportes")
def api_cierre_pdf(cierre_id: int):
    conn = get_db()
    c = conn.execute("SELECT * FROM cierres WHERE id=?;", (cierre_id,)).fetchone()
    if c is None:
        abort(404)
    ruta = ruta_reporte(c["pdf_filename"])
    if ruta:
        return send_file(ruta, mimetype="application/pdf", as_attachment=True,
                         download_name=f"Cierre_{c['jornada']}_{cierre_id}.pdf")
    datos = pdf_cierre(conn, c["jornada"], True, cierre_id=cierre_id)
    return send_file(io.BytesIO(datos), mimetype="application/pdf", as_attachment=True,
                     download_name=f"Cierre_{c['jornada']}_{cierre_id}.pdf")


@bp.get("/api/ahora")
def api_ahora():
    return ok(ahora=datetime.now().isoformat(timespec="seconds"))
