"""Inventario: insumos, movimientos de stock, recetas y la planilla de costos."""
from __future__ import annotations

import io
import sqlite3
from datetime import datetime
from typing import Any, Dict

from flask import Blueprint, request, send_file

from .. import auth, config, planilla
from ..db import ahora_iso, get_db, respaldo_automatico
from ..dominio import liquidacion as liq
from ..dominio import stock
from ..dominio.unidades import a_compra, a_receta, admite_hamburguesa, singular, unidad_receta
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
        en = unidad_receta(i["unidad"], i["porcion_g"])
        x["receta_unidad"], x["receta_unidad_1"], x["receta_factor"] = en["plural"], en["singular"], en["factor"]
        x["unidad_1"] = singular(i["unidad"]) if i["unidad"] not in ("kg", "g", "l", "ml") else i["unidad"]
        x["hamburguesas"] = (int(max(0.0, x["disponible"]) / en["factor"] + 1e-9) if en["hamburguesa"] else None)
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
    unidad = texto(d, "unidad", 20) or "unidades"
    porcion = numero(d, "porcion_g", 0.0)
    if porcion < 0:
        return None, "El peso de la hamburguesa no puede ser negativo."
    return {"nombre": nombre, "categoria": texto(d, "categoria", 40), "unidad": unidad,
            "stock_minimo": minimo, "costo_unit_cents": pesos(d, "costo_unit"),
            "porcion_g": porcion if admite_hamburguesa(unidad) else 0.0,
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
            "INSERT INTO insumos(nombre, categoria, unidad, stock, stock_minimo, costo_unit_cents, activo, porcion_g) "
            "VALUES(?,?,?,0,?,?,?,?);",
            (x["nombre"], x["categoria"], x["unidad"], x["stock_minimo"], x["costo_unit_cents"], x["activo"],
             x["porcion_g"]))
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
    antes = conn.execute("SELECT * FROM insumos WHERE id=?;", (iid,)).fetchone()
    if antes is None:
        return error("Ese insumo ya no existe.", 404)
    # De kg a g (o de l a ml): el stock, las recetas y los extras pasan a la unidad nueva, así no cambia lo
    # que hay. El costo y el mínimo, solo si no los tocaron en el formulario (si los tocaron, manda lo escrito).
    costo_igual = x["costo_unit_cents"] == int(antes["costo_unit_cents"] or 0)
    minimo_igual = x["stock_minimo"] == float(antes["stock_minimo"] or 0)
    f = stock.convertir_unidad(conn, iid, antes["unidad"], x["unidad"], costo=costo_igual)
    if f not in (None, 1.0):
        fila = conn.execute("SELECT costo_unit_cents, stock_minimo FROM insumos WHERE id=?;", (iid,)).fetchone()
        if costo_igual:
            x["costo_unit_cents"] = int(fila["costo_unit_cents"])
        if minimo_igual:
            x["stock_minimo"] = float(fila["stock_minimo"])
    # Si cambia el peso de la hamburguesa, cada receta sigue llevando las mismas hamburguesas.
    viejo = float(antes["porcion_g"] or 0)
    if viejo > 0 and x["porcion_g"] > 0 and viejo != x["porcion_g"]:
        k = x["porcion_g"] / viejo
        conn.execute("UPDATE recetas SET cantidad=ROUND(cantidad*?, 6) WHERE insumo_id=?;", (k, iid))
        conn.execute("UPDATE ingrediente_insumo SET cantidad=ROUND(cantidad*?, 6) WHERE insumo_id=?;", (k, iid))
    try:
        conn.execute(
            "UPDATE insumos SET nombre=?, categoria=?, unidad=?, stock_minimo=?, costo_unit_cents=?, activo=?, "
            "porcion_g=? WHERE id=?;",
            (x["nombre"], x["categoria"], x["unidad"], x["stock_minimo"], x["costo_unit_cents"], x["activo"],
             x["porcion_g"], iid))
    except sqlite3.IntegrityError:
        conn.rollback()
        return error(f"Ya existe otro insumo llamado «{x['nombre']}».", 409)
    conn.commit()
    return ok()


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
    for r in conn.execute("SELECT r.*, i.nombre, i.unidad, i.porcion_g FROM recetas r JOIN insumos i "
                          "ON i.id=r.insumo_id ORDER BY i.nombre;").fetchall():
        rec.setdefault(int(r["product_id"]), []).append(
            {"insumo_id": int(r["insumo_id"]), "cantidad": float(r["cantidad"]), "nombre": r["nombre"],
             "unidad": r["unidad"], "receta_unidad": unidad_receta(r["unidad"], r["porcion_g"])["plural"],
             "receta_unidad_1": unidad_receta(r["unidad"], r["porcion_g"])["singular"],
             "cantidad_receta": a_receta(r["cantidad"], r["unidad"], r["porcion_g"])})
    ligados = {}
    for r in conn.execute("SELECT l.*, i.unidad, i.porcion_g FROM ingrediente_insumo l "
                          "JOIN insumos i ON i.id=l.insumo_id;").fetchall():
        ligados[int(r["ingredient_id"])] = {
            "insumo_id": int(r["insumo_id"]), "cantidad": float(r["cantidad"]),
            "receta_unidad": unidad_receta(r["unidad"], r["porcion_g"])["plural"],
            "receta_unidad_1": unidad_receta(r["unidad"], r["porcion_g"])["singular"],
            "cantidad_receta": a_receta(r["cantidad"], r["unidad"], r["porcion_g"])}
    return ok(
        productos=[{"id": int(p["id"]), "name": p["name"], "active": int(p["active"]), "category": p["category"],
                    "receta": rec.get(int(p["id"]), [])}
                   for p in conn.execute("SELECT * FROM products ORDER BY active DESC, name COLLATE NOCASE;")],
        ingredientes=[{"id": int(i["id"]), "name": i["name"], "active": int(i["active"]),
                       "insumo": ligados.get(int(i["id"]))}
                      for i in conn.execute("SELECT * FROM ingredients ORDER BY active DESC, name COLLATE NOCASE;")],
    )


def _a_compra(conn: sqlite3.Connection, insumo_id: int, cantidad: float, en_receta: bool) -> Any:
    """Con `en_receta`, la cantidad viene como se escribe en la receta (g, ml, hamburguesas)."""
    i = conn.execute("SELECT unidad, porcion_g FROM insumos WHERE id=?;", (insumo_id,)).fetchone()
    if i is None:
        return None
    return a_compra(cantidad, i["unidad"], i["porcion_g"]) if en_receta else cantidad


@bp.put("/api/recetas/producto/<int:pid>")
@auth.requiere("inventario")
def api_receta_guardar(pid: int):
    d = cuerpo()
    lineas = d.get("receta") if isinstance(d.get("receta"), list) else []
    en_receta = bandera(d, "en_unidad_receta")
    conn = get_db()
    if not conn.execute("SELECT 1 FROM products WHERE id=?;", (pid,)).fetchone():
        return error("Ese producto ya no existe.", 404)
    conn.execute("DELETE FROM recetas WHERE product_id=?;", (pid,))
    for ln in lineas:
        try:
            insumo, cant = int(ln.get("insumo_id")), float(str(ln.get("cantidad")).replace(",", "."))
        except (TypeError, ValueError, AttributeError):
            continue
        cant = _a_compra(conn, insumo, cant, en_receta) if cant > 0 else None
        if cant:
            conn.execute("INSERT OR REPLACE INTO recetas(product_id, insumo_id, cantidad) VALUES(?,?,?);",
                         (pid, insumo, cant))
    conn.commit()
    return ok()


@bp.put("/api/recetas/ingrediente/<int:ing_id>")
@auth.requiere("inventario")
def api_ingrediente_insumo(ing_id: int):
    """Qué materia prima usa un ingrediente de la caja y cuánto suma como extra. Con cantidad 0 queda
    ligado solo para el «sin …» (el producto deja de llevar esa materia prima)."""
    d = cuerpo()
    conn = get_db()
    conn.execute("DELETE FROM ingrediente_insumo WHERE ingredient_id=?;", (ing_id,))
    try:
        insumo, cant = int(d.get("insumo_id") or 0), float(str(d.get("cantidad") or 0).replace(",", "."))
    except (TypeError, ValueError):
        insumo, cant = 0, 0.0
    if insumo and cant >= 0:
        cant = _a_compra(conn, insumo, cant, bandera(d, "en_unidad_receta"))
        if cant is not None:
            conn.execute("INSERT INTO ingrediente_insumo(ingredient_id, insumo_id, cantidad) VALUES(?,?,?);",
                         (ing_id, insumo, cant))
    conn.commit()
    return ok()


# ---------------------------------------------------------------------------
# Planilla de costos (P-019): Configuración → Cargar archivos
# ---------------------------------------------------------------------------
@bp.get("/api/planilla-costos.xlsx")
@auth.requiere("configuracion")
def api_planilla_descargar():
    conn = get_db()
    cfg = config.todas(conn)
    datos = planilla.generar(conn, cfg["unidades_stock"], cfg["negocio_nombre"], cfg["moneda_simbolo"])
    return send_file(io.BytesIO(datos), as_attachment=True,
                     download_name=f"planilla_de_costos_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.post("/api/planilla-costos")
@auth.requiere("configuracion")
def api_planilla_subir():
    """Revisa la planilla entera; si algo no se entiende no cambia nada y dice en qué fila está."""
    f = request.files.get("archivo")
    if f is None or not f.filename:
        return error("Elegí la planilla (.xlsx) para subir.")
    conn = get_db()
    try:
        hojas = planilla.leer_xlsx(f.read())
        plan = planilla.revisar(conn, hojas, config.obtener(conn, "unidades_stock"))
    except planilla.PlanillaInvalida as e:
        n = len(e.errores)
        return error(e.errores[0] if n == 1 else f"La planilla tiene {n} cosas para corregir. No se cambió nada.",
                     errores=e.errores)
    respaldo_automatico("antes-de-planilla", config.obtener(conn, "respaldos_a_conservar"))
    try:
        r = planilla.aplicar(conn, plan)
        auth.auditar(conn, "Planilla de costos subida",
                     f"{r['materias_nuevas']} materia(s) prima nueva(s), {r['materias_actualizadas']} actualizada(s), "
                     f"{r['productos_con_receta']} producto(s) con receta, {r['extras']} extra(s)")
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        return error(f"No se pudo guardar la planilla (base de datos: {e}). No se cambió nada.", 500)
    return ok(resultado=r)
