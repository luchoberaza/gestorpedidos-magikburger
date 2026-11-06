"""Inventario: insumos, movimientos de stock y recetas."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict

from flask import Blueprint, request

from .. import auth
from ..db import ahora_iso, get_db
from ..dominio import liquidacion as liq
from ..dominio import stock
from .util import bandera, cuerpo, error, numero, ok, pesos, texto

bp = Blueprint("inventario", __name__)


def _insumos(conn: sqlite3.Connection) -> list:
    comp = stock.comprometido(conn)
    out = []
    for i in conn.execute("SELECT * FROM insumos ORDER BY activo DESC, nombre COLLATE NOCASE;").fetchall():
        x = dict(i)
        x["comprometido"] = comp.get(int(i["id"]), 0.0)
        x["disponible"] = round(float(i["stock"] or 0) - x["comprometido"], 3)
        x["bajo"] = bool(float(i["stock_minimo"] or 0) > 0 and x["disponible"] <= float(i["stock_minimo"] or 0))
        x["valor_cents"] = int(round(max(0.0, float(i["stock"] or 0)) * int(i["costo_unit_cents"] or 0)))
        out.append(x)
    return out


@bp.get("/api/insumos")
@auth.requiere("inventario", "catalogo")
def api_insumos():
    conn = get_db()
    lista = _insumos(conn)
    return ok(insumos=lista, valor_total_cents=sum(i["valor_cents"] for i in lista if i["activo"]),
              bajos=sum(1 for i in lista if i["bajo"] and i["activo"]))


def _datos(d: Dict[str, Any]) -> Any:
    nombre = texto(d, "nombre", 80)
    if not nombre:
        return None, "Escribí el nombre del insumo."
    minimo = numero(d, "stock_minimo", 0.0)
    if minimo < 0:
        return None, "El stock mínimo no puede ser negativo."
    return {"nombre": nombre, "categoria": texto(d, "categoria", 40), "unidad": texto(d, "unidad", 20) or "unidades",
            "stock_minimo": minimo, "costo_unit_cents": pesos(d, "costo_unit"),
            "activo": 0 if not bandera(d, "activo", True) else 1}, None


@bp.post("/api/insumos")
@auth.requiere("inventario")
def api_insumo_nuevo():
    d = cuerpo()
    x, err = _datos(d)
    if err:
        return error(err)
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO insumos(nombre, categoria, unidad, stock, stock_minimo, costo_unit_cents, activo) "
            "VALUES(?,?,?,0,?,?,?);",
            (x["nombre"], x["categoria"], x["unidad"], x["stock_minimo"], x["costo_unit_cents"], x["activo"]))
    except sqlite3.IntegrityError:
        return error(f"Ya existe un insumo llamado «{x['nombre']}».", 409)
    iid = int(cur.lastrowid)
    inicial = numero(d, "stock", 0.0)
    if inicial:
        stock.mover(conn, iid, inicial, "entrada", "Stock inicial", auth.usuario_actual())
    conn.commit()
    return ok(id=iid)


@bp.put("/api/insumos/<int:iid>")
@auth.requiere("inventario")
def api_insumo_editar(iid: int):
    x, err = _datos(cuerpo())
    if err:
        return error(err)
    conn = get_db()
    try:
        n = conn.execute(
            "UPDATE insumos SET nombre=?, categoria=?, unidad=?, stock_minimo=?, costo_unit_cents=?, activo=? WHERE id=?;",
            (x["nombre"], x["categoria"], x["unidad"], x["stock_minimo"], x["costo_unit_cents"], x["activo"], iid)).rowcount
    except sqlite3.IntegrityError:
        return error(f"Ya existe otro insumo llamado «{x['nombre']}».", 409)
    conn.commit()
    return ok() if n else error("Ese insumo ya no existe.", 404)


@bp.delete("/api/insumos/<int:iid>")
@auth.requiere("inventario")
def api_insumo_borrar(iid: int):
    """Si tiene historial o está en recetas, se desactiva en vez de borrarse."""
    conn = get_db()
    i = conn.execute("SELECT * FROM insumos WHERE id=?;", (iid,)).fetchone()
    if i is None:
        return error("Ese insumo ya no existe.", 404)
    usado = (conn.execute("SELECT 1 FROM movimientos_stock WHERE insumo_id=? LIMIT 1;", (iid,)).fetchone()
             or conn.execute("SELECT 1 FROM recetas WHERE insumo_id=? LIMIT 1;", (iid,)).fetchone()
             or conn.execute("SELECT 1 FROM ingrediente_insumo WHERE insumo_id=? LIMIT 1;", (iid,)).fetchone())
    if usado:
        conn.execute("UPDATE insumos SET activo=0 WHERE id=?;", (iid,))
        msg = "Tenía historial o recetas, así que quedó desactivado (no se pierde nada)."
    else:
        conn.execute("DELETE FROM insumos WHERE id=?;", (iid,))
        msg = "Insumo eliminado."
    auth.auditar(conn, "Insumo eliminado", i["nombre"])
    conn.commit()
    return ok(mensaje=msg)


@bp.post("/api/insumos/<int:iid>/movimiento")
@auth.requiere("inventario")
def api_movimiento(iid: int):
    """tipo: entrada (compra) · salida (merma/uso) · conteo (fija el stock contado)."""
    d = cuerpo()
    tipo = texto(d, "tipo", 20)
    cantidad = numero(d, "cantidad", 0.0)
    conn = get_db()
    i = conn.execute("SELECT * FROM insumos WHERE id=?;", (iid,)).fetchone()
    if i is None:
        return error("Ese insumo ya no existe.", 404)
    u = auth.usuario_actual()
    if tipo == "conteo":
        if cantidad < 0:
            return error("El stock contado no puede ser negativo.")
        delta = round(cantidad - float(i["stock"] or 0), 3)
        if delta == 0:
            return ok(stock=float(i["stock"]), mensaje="El stock ya coincidía con lo contado.")
        motivo = texto(d, "motivo", 160) or "Conteo físico"
        tipo_mov = "ajuste"
    elif tipo in ("entrada", "salida"):
        if cantidad <= 0:
            return error("Ingresá una cantidad mayor que cero.")
        delta = cantidad if tipo == "entrada" else -cantidad
        motivo = texto(d, "motivo", 160) or ("Compra / reposición" if tipo == "entrada" else "Merma / uso interno")
        tipo_mov = tipo
    else:
        return error("Tipo de movimiento inválido.")
    nuevo = stock.mover(conn, iid, delta, tipo_mov, motivo, u)
    gasto_id = None
    costo_total = pesos(d, "costo_total")
    if tipo == "entrada" and bandera(d, "registrar_gasto") and costo_total > 0:
        cur = conn.execute(
            "INSERT INTO gastos(fecha, categoria, descripcion, proveedor, importe_cents, metodo_pago, observaciones, "
            "created_at, user_id) VALUES(?,?,?,?,?,?,?,?,?);",
            (liq.jornada_actual(conn), texto(d, "categoria", 40) or i["categoria"] or "Ingredientes",
             f"Compra de {i['nombre']} ({cantidad:g} {i['unidad']})", texto(d, "proveedor", 80), costo_total,
             texto(d, "metodo_pago", 40), "Registrado desde Inventario", ahora_iso(), u["id"] if u else None))
        gasto_id = cur.lastrowid
        conn.execute("UPDATE insumos SET costo_unit_cents=? WHERE id=?;",
                     (int(round(costo_total / cantidad)), iid))
    conn.commit()
    return ok(stock=nuevo, gasto_id=gasto_id)


@bp.get("/api/movimientos")
@auth.requiere("inventario")
def api_movimientos():
    conn = get_db()
    iid = request.args.get("insumo")
    sql = ("SELECT m.*, i.nombre AS insumo, i.unidad FROM movimientos_stock m "
           "LEFT JOIN insumos i ON i.id=m.insumo_id")
    params: list = []
    if iid:
        sql += " WHERE m.insumo_id=?"
        params.append(int(iid))
    sql += " ORDER BY m.fecha DESC, m.id DESC LIMIT 300;"
    return ok(movimientos=[dict(r) for r in conn.execute(sql, params).fetchall()])


# ---------------------------------------------------------------------------
# Recetas: qué consume cada producto y cada extra
# ---------------------------------------------------------------------------
@bp.get("/api/recetas")
@auth.requiere("inventario", "catalogo")
def api_recetas():
    conn = get_db()
    rec: Dict[int, list] = {}
    for r in conn.execute("SELECT r.*, i.nombre, i.unidad FROM recetas r JOIN insumos i ON i.id=r.insumo_id "
                          "ORDER BY i.nombre;").fetchall():
        rec.setdefault(int(r["product_id"]), []).append(
            {"insumo_id": int(r["insumo_id"]), "cantidad": float(r["cantidad"]), "nombre": r["nombre"],
             "unidad": r["unidad"]})
    ligados = {int(r["ingredient_id"]): {"insumo_id": int(r["insumo_id"]), "cantidad": float(r["cantidad"])}
               for r in conn.execute("SELECT * FROM ingrediente_insumo;").fetchall()}
    return ok(
        productos=[{"id": int(p["id"]), "name": p["name"], "active": int(p["active"]), "category": p["category"],
                    "receta": rec.get(int(p["id"]), [])}
                   for p in conn.execute("SELECT * FROM products ORDER BY active DESC, name COLLATE NOCASE;")],
        ingredientes=[{"id": int(i["id"]), "name": i["name"], "active": int(i["active"]),
                       "insumo": ligados.get(int(i["id"]))}
                      for i in conn.execute("SELECT * FROM ingredients ORDER BY active DESC, name COLLATE NOCASE;")],
    )


@bp.put("/api/recetas/producto/<int:pid>")
@auth.requiere("inventario")
def api_receta_guardar(pid: int):
    d = cuerpo()
    lineas = d.get("receta") if isinstance(d.get("receta"), list) else []
    conn = get_db()
    if not conn.execute("SELECT 1 FROM products WHERE id=?;", (pid,)).fetchone():
        return error("Ese producto ya no existe.", 404)
    conn.execute("DELETE FROM recetas WHERE product_id=?;", (pid,))
    for ln in lineas:
        try:
            insumo, cant = int(ln.get("insumo_id")), float(str(ln.get("cantidad")).replace(",", "."))
        except (TypeError, ValueError, AttributeError):
            continue
        if cant > 0 and conn.execute("SELECT 1 FROM insumos WHERE id=?;", (insumo,)).fetchone():
            conn.execute("INSERT OR REPLACE INTO recetas(product_id, insumo_id, cantidad) VALUES(?,?,?);",
                         (pid, insumo, cant))
    conn.commit()
    return ok()


@bp.put("/api/recetas/ingrediente/<int:ing_id>")
@auth.requiere("inventario")
def api_ingrediente_insumo(ing_id: int):
    d = cuerpo()
    conn = get_db()
    conn.execute("DELETE FROM ingrediente_insumo WHERE ingredient_id=?;", (ing_id,))
    try:
        insumo, cant = int(d.get("insumo_id") or 0), float(str(d.get("cantidad") or 0).replace(",", "."))
    except (TypeError, ValueError):
        insumo, cant = 0, 0.0
    if insumo and cant > 0:
        conn.execute("INSERT INTO ingrediente_insumo(ingredient_id, insumo_id, cantidad) VALUES(?,?,?);",
                     (ing_id, insumo, cant))
    conn.commit()
    return ok()
