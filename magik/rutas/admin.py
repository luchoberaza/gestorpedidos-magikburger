"""Administración: catálogo, repartidores, usuarios, configuración, respaldo e importación."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
from datetime import datetime
from typing import Any, Dict, List

from flask import Blueprint, request, send_file

from .. import auth, config
from ..db import APP_DIR, carpeta, carpeta_datos, conectar, copiar_base, get_db, migrar, respaldo_automatico, ruta_db
from ..dominio.pedidos import money
from ..importar_caja import importar as importar_caja
from ..ticket import bytes_prueba, enviar_crudo, impresora_predeterminada, listar_impresoras, ruta_logo_propio
from .util import bandera, cuerpo, entero, error, ok, pesos, texto

bp = Blueprint("admin", __name__)


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------
@bp.get("/api/admin/catalogo")
@auth.requiere("catalogo")
def api_catalogo():
    conn = get_db()
    base: Dict[int, List[int]] = {}
    for r in conn.execute("SELECT * FROM product_base_ingredients;"):
        base.setdefault(int(r["product_id"]), []).append(int(r["ingredient_id"]))
    comp: Dict[int, List[int]] = {}
    for r in conn.execute("SELECT * FROM promo_components ORDER BY sort_order, id;"):
        comp.setdefault(int(r["promo_id"]), []).append(int(r["product_id"]))
    usados = {int(r[0]) for r in conn.execute("SELECT DISTINCT product_id FROM order_items;")}
    return ok(
        productos=[{**dict(p), "base_ingredient_ids": base.get(int(p["id"]), []), "usado": int(p["id"]) in usados}
                   for p in conn.execute("SELECT * FROM products ORDER BY active DESC, sort_order, name COLLATE NOCASE;")],
        ingredientes=[dict(i) for i in conn.execute("SELECT * FROM ingredients ORDER BY active DESC, name COLLATE NOCASE;")],
        promos=[{**dict(p), "component_product_ids": comp.get(int(p["id"]), [])}
                for p in conn.execute("SELECT * FROM promos ORDER BY active DESC, name COLLATE NOCASE;")],
        categorias=sorted({(p["category"] or "").strip() for p in conn.execute("SELECT category FROM products;")} - {""}),
    )


def _ids(valor: Any) -> List[int]:
    out = []
    for x in valor if isinstance(valor, list) else []:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            pass
    return out


def _guardar_producto(conn: sqlite3.Connection, pid: Any, d: Dict[str, Any]) -> Any:
    nombre = texto(d, "name", 80)
    if not nombre:
        return None, "Escribí el nombre del producto."
    precio = pesos(d, "price")
    if precio <= 0:
        return None, "El precio tiene que ser mayor que cero."
    activo = 1 if bandera(d, "active", True) else 0
    cat = texto(d, "category", 40)
    cur = conn.cursor()
    if pid is None:
        cur.execute("INSERT INTO products(name, price_cents, active, category) VALUES(?,?,?,?);",
                    (nombre, precio, activo, cat))
        pid = int(cur.lastrowid)
    else:
        if not cur.execute("UPDATE products SET name=?, price_cents=?, active=?, category=? WHERE id=?;",
                           (nombre, precio, activo, cat, pid)).rowcount:
            return None, "Ese producto ya no existe."
    cur.execute("DELETE FROM product_base_ingredients WHERE product_id=?;", (pid,))
    for iid in set(_ids(d.get("base_ingredient_ids"))):
        cur.execute("INSERT OR IGNORE INTO product_base_ingredients(product_id, ingredient_id) VALUES(?,?);", (pid, iid))
    return pid, None


@bp.post("/api/admin/productos")
@auth.requiere("catalogo")
def api_producto_nuevo():
    conn = get_db()
    pid, err = _guardar_producto(conn, None, cuerpo())
    if err:
        return error(err)
    conn.commit()
    return ok(id=pid)


@bp.put("/api/admin/productos/<int:pid>")
@auth.requiere("catalogo")
def api_producto_editar(pid: int):
    conn = get_db()
    viejo = conn.execute("SELECT price_cents, name FROM products WHERE id=?;", (pid,)).fetchone()
    _, err = _guardar_producto(conn, pid, cuerpo())
    if err:
        return error(err, 404 if "no existe" in err else 400)
    nuevo = conn.execute("SELECT price_cents FROM products WHERE id=?;", (pid,)).fetchone()
    if viejo and nuevo and int(viejo["price_cents"]) != int(nuevo["price_cents"]):
        auth.auditar(conn, "Precio cambiado", f"{viejo['name']}: ${money(viejo['price_cents'])} → ${money(nuevo['price_cents'])}")
    conn.commit()
    return ok()


@bp.delete("/api/admin/productos/<int:pid>")
@auth.requiere("catalogo")
def api_producto_borrar(pid: int):
    """Si está en pedidos o promos, queda inactivo: nunca se rompe el historial."""
    conn = get_db()
    usado = (conn.execute("SELECT 1 FROM order_items WHERE product_id=? LIMIT 1;", (pid,)).fetchone()
             or conn.execute("SELECT 1 FROM promo_components WHERE product_id=? LIMIT 1;", (pid,)).fetchone())
    if usado:
        conn.execute("UPDATE products SET active=0 WHERE id=?;", (pid,))
        msg = "Tiene pedidos o promos, así que quedó desactivado (no se pierde el historial)."
    else:
        for t in ("product_base_ingredients", "recetas"):
            conn.execute(f"DELETE FROM {t} WHERE product_id=?;", (pid,))
        conn.execute("DELETE FROM products WHERE id=?;", (pid,))
        msg = "Producto eliminado."
    conn.commit()
    return ok(mensaje=msg)


# ---------------------------------------------------------------------------
# Promos
# ---------------------------------------------------------------------------
def _guardar_promo(conn: sqlite3.Connection, prid: Any, d: Dict[str, Any]) -> Any:
    nombre = texto(d, "name", 80)
    if not nombre:
        return None, "Escribí el nombre de la promo."
    precio = pesos(d, "price")
    if precio <= 0:
        return None, "El precio de la promo tiene que ser mayor que cero."
    comps = _ids(d.get("component_product_ids"))[:40]
    if not comps:
        return None, "Agregá al menos un producto a la promo."
    activo = 1 if bandera(d, "active", True) else 0
    cur = conn.cursor()
    if prid is None:
        cur.execute("INSERT INTO promos(name, price_cents, active) VALUES(?,?,?);", (nombre, precio, activo))
        prid = int(cur.lastrowid)
    elif not cur.execute("UPDATE promos SET name=?, price_cents=?, active=? WHERE id=?;",
                         (nombre, precio, activo, prid)).rowcount:
        return None, "Esa promo ya no existe."
    cur.execute("DELETE FROM promo_components WHERE promo_id=?;", (prid,))
    for i, pid in enumerate(comps):
        cur.execute("INSERT INTO promo_components(promo_id, product_id, sort_order) VALUES(?,?,?);", (prid, pid, i))
    return prid, None


@bp.post("/api/admin/promos")
@auth.requiere("catalogo")
def api_promo_nueva():
    conn = get_db()
    prid, err = _guardar_promo(conn, None, cuerpo())
    if err:
        return error(err)
    conn.commit()
    return ok(id=prid)


@bp.put("/api/admin/promos/<int:prid>")
@auth.requiere("catalogo")
def api_promo_editar(prid: int):
    conn = get_db()
    _, err = _guardar_promo(conn, prid, cuerpo())
    if err:
        return error(err)
    conn.commit()
    return ok()


@bp.delete("/api/admin/promos/<int:prid>")
@auth.requiere("catalogo")
def api_promo_borrar(prid: int):
    conn = get_db()
    conn.execute("DELETE FROM promo_components WHERE promo_id=?;", (prid,))
    conn.execute("DELETE FROM promos WHERE id=?;", (prid,))
    conn.commit()
    return ok(mensaje="Promo eliminada. Los pedidos que ya la usaron conservan su nombre y precio.")


# ---------------------------------------------------------------------------
# Ingredientes / extras
# ---------------------------------------------------------------------------
def _guardar_ingrediente(conn: sqlite3.Connection, iid: Any, d: Dict[str, Any]) -> Any:
    nombre = texto(d, "name", 60)
    if not nombre:
        return None, "Escribí el nombre del ingrediente."
    precio = pesos(d, "extra_price")
    activo = 1 if bandera(d, "active", True) else 0
    if iid is None:
        cur = conn.execute("INSERT INTO ingredients(name, extra_price_cents, active) VALUES(?,?,?);",
                           (nombre, precio, activo))
        return int(cur.lastrowid), None
    if not conn.execute("UPDATE ingredients SET name=?, extra_price_cents=?, active=? WHERE id=?;",
                        (nombre, precio, activo, iid)).rowcount:
        return None, "Ese ingrediente ya no existe."
    return iid, None


@bp.post("/api/admin/ingredientes")
@auth.requiere("catalogo")
def api_ing_nuevo():
    conn = get_db()
    iid, err = _guardar_ingrediente(conn, None, cuerpo())
    if err:
        return error(err)
    conn.commit()
    return ok(id=iid)


@bp.put("/api/admin/ingredientes/<int:iid>")
@auth.requiere("catalogo")
def api_ing_editar(iid: int):
    conn = get_db()
    _, err = _guardar_ingrediente(conn, iid, cuerpo())
    if err:
        return error(err)
    conn.commit()
    return ok()


@bp.delete("/api/admin/ingredientes/<int:iid>")
@auth.requiere("catalogo")
def api_ing_borrar(iid: int):
    conn = get_db()
    usado = (conn.execute("SELECT 1 FROM product_base_ingredients WHERE ingredient_id=? LIMIT 1;", (iid,)).fetchone()
             or conn.execute("SELECT 1 FROM order_item_mods WHERE ingredient_id=? LIMIT 1;", (iid,)).fetchone())
    if usado:
        conn.execute("UPDATE ingredients SET active=0 WHERE id=?;", (iid,))
        msg = "Está en productos o en pedidos, así que quedó desactivado."
    else:
        conn.execute("DELETE FROM ingrediente_insumo WHERE ingredient_id=?;", (iid,))
        conn.execute("DELETE FROM ingredients WHERE id=?;", (iid,))
        msg = "Ingrediente eliminado."
    conn.commit()
    return ok(mensaje=msg)


# ---------------------------------------------------------------------------
# Repartidores
# ---------------------------------------------------------------------------
@bp.get("/api/admin/repartidores")
@auth.requiere("repartidores")
def api_repartidores():
    conn = get_db()
    return ok(repartidores=[dict(c) for c in conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM orders o WHERE o.courier_id=c.id) AS pedidos "
        "FROM couriers c ORDER BY c.active DESC, c.name COLLATE NOCASE;")],
        cambio_default_cents=config.obtener(conn, "cambio_default_cents"))


def _datos_rep(conn: sqlite3.Connection, d: Dict[str, Any]) -> Any:
    nombre = texto(d, "name", 60)
    if not nombre:
        return None, "Escribí el nombre del repartidor."
    return {"name": nombre, "cash_float_cents": pesos(d, "cash_float", config.obtener(conn, "cambio_default_cents")),
            "active": 1 if bandera(d, "active", True) else 0}, None


@bp.post("/api/admin/repartidores")
@auth.requiere("repartidores")
def api_rep_nuevo():
    conn = get_db()
    x, err = _datos_rep(conn, cuerpo())
    if err:
        return error(err)
    cur = conn.execute("INSERT INTO couriers(name, cash_float_cents, active) VALUES(?,?,?);",
                       (x["name"], x["cash_float_cents"], x["active"]))
    conn.commit()
    return ok(id=cur.lastrowid)


@bp.put("/api/admin/repartidores/<int:cid>")
@auth.requiere("repartidores")
def api_rep_editar(cid: int):
    conn = get_db()
    x, err = _datos_rep(conn, cuerpo())
    if err:
        return error(err)
    conn.execute("UPDATE couriers SET name=?, cash_float_cents=?, active=? WHERE id=?;",
                 (x["name"], x["cash_float_cents"], x["active"], cid))
    conn.commit()
    return ok()


@bp.delete("/api/admin/repartidores/<int:cid>")
@auth.requiere("repartidores")
def api_rep_borrar(cid: int):
    """Con pedidos en el historial no se borra (se perdería quién los llevó): se desactiva."""
    conn = get_db()
    if conn.execute("SELECT 1 FROM orders WHERE courier_id=? LIMIT 1;", (cid,)).fetchone():
        conn.execute("UPDATE couriers SET active=0 WHERE id=?;", (cid,))
        msg = "Tiene pedidos registrados, así que quedó desactivado (deja de aparecer en la caja)."
    else:
        conn.execute("DELETE FROM couriers WHERE id=?;", (cid,))
        msg = "Repartidor eliminado."
    conn.commit()
    return ok(mensaje=msg)


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
def _admins_activos(conn: sqlite3.Connection, excepto: int = 0) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND id<>?;",
                            (excepto,)).fetchone()[0])


@bp.get("/api/usuarios")
@auth.requiere("usuarios")
def api_usuarios():
    conn = get_db()
    libre = config.obtener(conn, "acceso_libre_usuario_id")
    return ok(usuarios=[{
        "id": int(u["id"]), "username": u["username"], "name": u["name"], "role": u["role"],
        "role_label": auth.ROLES.get(u["role"], u["role"]), "permisos": auth.permisos_de(u),
        "active": int(u["active"]), "tiene_clave": bool(u["password_hash"]), "last_login": u["last_login"],
        "acceso_libre": str(u["id"]) == str(libre),
    } for u in conn.execute("SELECT * FROM users ORDER BY active DESC, role='admin' DESC, name;")],
        roles=auth.ROLES, presets=auth.PRESETS,
        permisos={k: {"label": v[0], "grupo": v[1]} for k, v in auth.PERMISOS.items()})


def _guardar_usuario(conn: sqlite3.Connection, uid: Any, d: Dict[str, Any]) -> Any:
    nombre = texto(d, "name", 60)
    usuario = texto(d, "username", 40).replace(" ", "")
    rol = texto(d, "role", 20)
    if rol not in auth.ROLES:
        return None, "Elegí un rol."
    if not usuario:
        return None, "Escribí el nombre de usuario (sin espacios)."
    permisos = [p for p in (d.get("permisos") or []) if p in auth.PERMISOS] if rol != "admin" else auth.PRESETS["admin"]
    clave = str(d.get("password") or "")
    if clave and len(clave) < 4:
        return None, "La contraseña tiene que tener al menos 4 caracteres."
    activo = 1 if bandera(d, "active", True) else 0
    yo = auth.usuario_actual()
    if uid is not None:
        actual = conn.execute("SELECT * FROM users WHERE id=?;", (uid,)).fetchone()
        if actual is None:
            return None, "Ese usuario ya no existe."
        if actual["role"] == "admin" and (rol != "admin" or not activo) and _admins_activos(conn, uid) == 0:
            return None, "Tiene que quedar al menos un administrador activo."
        if yo and int(yo["id"]) == int(uid) and not activo:
            return None, "No podés desactivar tu propio usuario."
    try:
        if uid is None:
            if not clave:
                return None, "Poné una contraseña para el usuario nuevo."
            cur = conn.execute(
                "INSERT INTO users(username, name, password_hash, role, permissions, active, created_at) "
                "VALUES(?,?,?,?,?,?,?);",
                (usuario, nombre or usuario, auth.hashear(clave), rol, json.dumps(permisos), activo,
                 datetime.now().isoformat(timespec="seconds")))
            uid = int(cur.lastrowid)
        else:
            conn.execute("UPDATE users SET username=?, name=?, role=?, permissions=?, active=? WHERE id=?;",
                         (usuario, nombre or usuario, rol, json.dumps(permisos), activo, uid))
            if clave:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?;", (auth.hashear(clave), uid))
    except sqlite3.IntegrityError:
        return None, f"Ya existe un usuario «{usuario}»."
    return uid, None


@bp.post("/api/usuarios")
@auth.requiere("usuarios")
def api_usuario_nuevo():
    conn = get_db()
    d = cuerpo()
    uid, err = _guardar_usuario(conn, None, d)
    if err:
        return error(err)
    auth.auditar(conn, "Usuario creado", f"{texto(d, 'username', 40)} ({texto(d, 'role', 20)})")
    conn.commit()
    return ok(id=uid)


@bp.put("/api/usuarios/<int:uid>")
@auth.requiere("usuarios")
def api_usuario_editar(uid: int):
    conn = get_db()
    d = cuerpo()
    _, err = _guardar_usuario(conn, uid, d)
    if err:
        return error(err)
    auth.auditar(conn, "Usuario modificado", texto(d, "username", 40) + (" (cambió la contraseña)" if d.get("password") else ""))
    conn.commit()
    return ok()


@bp.delete("/api/usuarios/<int:uid>")
@auth.requiere("usuarios")
def api_usuario_borrar(uid: int):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE id=?;", (uid,)).fetchone()
    if u is None:
        return error("Ese usuario ya no existe.", 404)
    yo = auth.usuario_actual()
    if yo and int(yo["id"]) == uid:
        return error("No podés eliminar tu propio usuario.")
    if u["role"] == "admin" and _admins_activos(conn, uid) == 0:
        return error("Tiene que quedar al menos un administrador activo.")
    if str(config.obtener(conn, "acceso_libre_usuario_id")) == str(uid):
        config.guardar(conn, {"acceso_libre_usuario_id": ""})
    conn.execute("DELETE FROM users WHERE id=?;", (uid,))
    auth.auditar(conn, "Usuario eliminado", u["username"])
    conn.commit()
    return ok()


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
@bp.get("/api/config")
@auth.requiere("configuracion")
def api_config():
    conn = get_db()
    return ok(config=config.todas(conn), logo_propio=bool(ruta_logo_propio()),
              carpeta_datos=carpeta_datos(), base=ruta_db(),
              metodos_pago=[dict(m) for m in conn.execute("SELECT * FROM metodos_pago ORDER BY sort_order, id;")],
              usuarios=[{"id": int(u["id"]), "name": u["name"] or u["username"]}
                        for u in conn.execute("SELECT * FROM users WHERE active=1 ORDER BY name;")])


@bp.post("/api/config")
@auth.requiere("configuracion")
def api_config_guardar():
    conn = get_db()
    d = cuerpo()
    guardado, errores = config.guardar(conn, d)
    if errores:
        conn.rollback()
        return error(" ".join(errores))
    conn.commit()
    return ok(config=config.todas(conn), guardado=list(guardado))


@bp.post("/api/config/preferencia")
@auth.requiere("pos", "pedidos")
def api_preferencia():
    """Ajustes que la caja cambia desde su pantalla (copias de ticket, envío por defecto)."""
    conn = get_db()
    d = {k: v for k, v in cuerpo().items() if k in ("ticket_copies", "delivery_fee_cents")}
    _, errores = config.guardar(conn, d)
    if errores:
        return error(" ".join(errores))
    conn.commit()
    return ok()


@bp.post("/api/settings")
@auth.requiere("pos", "pedidos")
def api_settings_compat():
    return api_preferencia()


@bp.post("/api/config/tema")
def api_tema():
    if auth.usuario_actual() is None:
        return error("Iniciá sesión.", 401)
    conn = get_db()
    _, errores = config.guardar(conn, {"tema": texto(cuerpo(), "tema", 10)})
    if errores:
        return error(" ".join(errores))
    conn.commit()
    return ok()


@bp.get("/marca/logo")
def marca_logo():
    p = ruta_logo_propio() or os.path.join(APP_DIR, "static", "logo.png")
    resp = send_file(p)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.post("/api/config/logo")
@auth.requiere("configuracion")
def api_logo():
    f = request.files.get("logo")
    if f is None or not f.filename:
        return error("Elegí una imagen.")
    ext = os.path.splitext(f.filename)[1].lower().lstrip(".")
    if ext not in ("png", "jpg", "jpeg", "webp"):
        return error("El logo tiene que ser PNG, JPG o WEBP.")
    datos = f.read()
    if len(datos) > 4 * 1024 * 1024:
        return error("La imagen pesa más de 4 MB. Usá una más liviana.")
    d = carpeta("marca")
    for viejo in os.listdir(d):
        if viejo.startswith("logo."):
            os.remove(os.path.join(d, viejo))
    with open(os.path.join(d, f"logo.{ext}"), "wb") as out:
        out.write(datos)
    return ok()


@bp.delete("/api/config/logo")
@auth.requiere("configuracion")
def api_logo_borrar():
    p = ruta_logo_propio()
    if p:
        os.remove(p)
    return ok()


# Medios de pago -------------------------------------------------------------
@bp.post("/api/metodos-pago")
@auth.requiere("configuracion")
def api_medio_nuevo():
    d = cuerpo()
    nombre = texto(d, "name", 40)
    if not nombre:
        return error("Escribí el nombre del medio de pago.")
    conn = get_db()
    base = "".join(ch for ch in nombre.lower() if ch.isalnum())[:20] or "medio"
    code, n = base, 2
    while conn.execute("SELECT 1 FROM metodos_pago WHERE code=?;", (code,)).fetchone():
        code, n = f"{base}{n}", n + 1
    orden = conn.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM metodos_pago;").fetchone()[0]
    conn.execute("INSERT INTO metodos_pago(code, name, is_cash, hide_prices_ticket, active, sort_order) "
                 "VALUES(?,?,?,?,1,?);",
                 (code, nombre, 1 if bandera(d, "is_cash") else 0, 1 if bandera(d, "hide_prices_ticket") else 0, orden))
    conn.commit()
    return ok(code=code)


@bp.put("/api/metodos-pago/<int:mid>")
@auth.requiere("configuracion")
def api_medio_editar(mid: int):
    d = cuerpo()
    nombre = texto(d, "name", 40)
    if not nombre:
        return error("Escribí el nombre del medio de pago.")
    conn = get_db()
    activo = 1 if bandera(d, "active", True) else 0
    if not activo and conn.execute("SELECT COUNT(*) FROM metodos_pago WHERE active=1 AND id<>?;", (mid,)).fetchone()[0] == 0:
        return error("Tiene que quedar al menos un medio de pago activo.")
    conn.execute("UPDATE metodos_pago SET name=?, is_cash=?, hide_prices_ticket=?, active=?, sort_order=? WHERE id=?;",
                 (nombre, 1 if bandera(d, "is_cash") else 0, 1 if bandera(d, "hide_prices_ticket") else 0, activo,
                  entero(d, "sort_order", 0), mid))
    conn.commit()
    return ok()


# Impresoras -----------------------------------------------------------------
@bp.get("/api/impresoras")
@auth.requiere("configuracion")
def api_impresoras():
    return ok(impresoras=listar_impresoras(), predeterminada=impresora_predeterminada())


@bp.post("/api/impresoras/prueba")
@auth.requiere("configuracion")
def api_impresora_prueba():
    conn = get_db()
    nombre = texto(cuerpo(), "printer", 120) or config.obtener(conn, "printer_name") or impresora_predeterminada() or ""
    if not nombre:
        return error("No hay impresora elegida ni predeterminada en Windows.")
    try:
        enviar_crudo(nombre, bytes_prueba(config.obtener(conn, "negocio_nombre")))
    except Exception as e:
        return error(f"No se pudo imprimir en «{nombre}»: {e}")
    return ok(printer=nombre)


# ---------------------------------------------------------------------------
# Respaldo e importación
# ---------------------------------------------------------------------------
@bp.get("/api/respaldo/descargar")
@auth.requiere("configuracion")
def api_respaldo_descargar():
    conn = get_db()
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        copiar_base(tmp)
        with open(tmp, "rb") as f:
            datos = f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    nombre = "".join(ch for ch in config.obtener(conn, "negocio_nombre") if ch.isalnum()) or "negocio"
    return send_file(io.BytesIO(datos), as_attachment=True, mimetype="application/octet-stream",
                     download_name=f"respaldo_{nombre}_{datetime.now().strftime('%Y-%m-%d_%H%M')}.db")


@bp.get("/api/respaldo/lista")
@auth.requiere("configuracion")
def api_respaldo_lista():
    d = carpeta("respaldos")
    archivos = sorted((f for f in os.listdir(d) if f.endswith(".db")), reverse=True)
    return ok(carpeta=d, respaldos=[{"nombre": f, "bytes": os.path.getsize(os.path.join(d, f))} for f in archivos[:60]])


@bp.get("/api/respaldo/archivo/<path:nombre>")
@auth.requiere("configuracion")
def api_respaldo_archivo(nombre: str):
    p = os.path.join(carpeta("respaldos"), os.path.basename(nombre))
    if not os.path.exists(p):
        return error("Ese respaldo ya no existe.", 404)
    return send_file(p, as_attachment=True, download_name=os.path.basename(p))


def _archivo_subido(campo: str) -> Any:
    f = request.files.get(campo)
    if f is None or not f.filename:
        return None, None, "Elegí un archivo."
    datos = f.read()
    if not datos.startswith(b"SQLite format 3"):
        return None, None, "El archivo no es una base de datos válida (.db)."
    fd, tmp = tempfile.mkstemp(suffix=".db")
    with os.fdopen(fd, "wb") as out:
        out.write(datos)
    return tmp, datos, None


@bp.post("/api/respaldo/restaurar")
@auth.requiere("configuracion")
def api_respaldo_restaurar():
    tmp, _datos, err = _archivo_subido("archivo")
    if err:
        return error(err)
    try:
        prueba = conectar(tmp)
        try:
            tablas = {r[0] for r in prueba.execute("SELECT name FROM sqlite_master WHERE type='table';")}
            sano = prueba.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        finally:
            prueba.close()
        if not {"orders", "products"} <= tablas:
            return error("Ese archivo no es un respaldo de esta app (no tiene pedidos ni productos).")
        if not sano:
            return error("El respaldo está dañado (falló la verificación de integridad).")
        previo = respaldo_automatico("antes-de-restaurar", 365)
        conn = get_db()
        conn.commit()
        src = conectar(tmp)
        try:
            src.backup(conn)
        finally:
            src.close()
        conn.execute("PRAGMA user_version;")
        migrar()
        auth.auditar(conn, "Respaldo restaurado", os.path.basename(previo or ""))
        conn.commit()
        return ok(respaldo_previo=os.path.basename(previo) if previo else None)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


@bp.post("/api/importar/caja")
@auth.requiere("configuracion")
def api_importar_caja():
    tmp, datos, err = _archivo_subido("archivo")
    if err:
        return error(err)
    try:
        conn = get_db()
        respaldo_automatico("antes-de-importar", 365)
        try:
            res = importar_caja(conn, tmp, datos)
        except ValueError as e:
            return error(str(e))
        auth.auditar(conn, "Importación desde Caja Hamburguesería", json.dumps(res))
        conn.commit()
        return ok(resultado=res)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


@bp.get("/api/auditoria")
@auth.requiere("configuracion", "usuarios")
def api_auditoria():
    return ok(eventos=[dict(r) for r in get_db().execute(
        "SELECT * FROM auditoria ORDER BY id DESC LIMIT 300;").fetchall()])


# ---------------------------------------------------------------------------
# Búsqueda global
# ---------------------------------------------------------------------------
@bp.get("/api/buscar")
def api_buscar():
    u = auth.usuario_actual()
    if u is None:
        return error("Iniciá sesión.", 401)
    q = str(request.args.get("q") or "").strip()
    if len(q) < 2:
        return ok(resultados=[])
    conn = get_db()
    like = f"%{q}%"
    p = u["permisos"]
    out: List[Dict[str, Any]] = []
    if "pedidos" in p or "ventas" in p:
        for o in conn.execute("SELECT id, address, phone, total_cents, created_at, cierre_id FROM orders "
                              "WHERE address LIKE ? OR phone LIKE ? OR CAST(id AS TEXT)=? "
                              "ORDER BY id DESC LIMIT 6;", (like, like, q)):
            out.append({"tipo": "Pedido", "titulo": f"#{o['id']} · {o['address']}",
                        "detalle": f"{o['phone']} · {str(o['created_at'])[:10]} · ${money(o['total_cents'])}"
                                   + (" · cerrado" if o["cierre_id"] else ""),
                        "ir": "pedidos" if o["cierre_id"] is None else "ventas", "id": o["id"]})
    if "gastos" in p:
        for g in conn.execute("SELECT * FROM gastos WHERE descripcion LIKE ? OR proveedor LIKE ? OR categoria LIKE ? "
                              "ORDER BY fecha DESC LIMIT 5;", (like, like, like)):
            out.append({"tipo": "Gasto", "titulo": g["descripcion"],
                        "detalle": f"{g['fecha']} · {g['categoria']} · ${money(g['importe_cents'])}", "ir": "gastos"})
    if "ventas" in p:
        for v in conn.execute("SELECT * FROM ventas_manuales WHERE concepto LIKE ? OR observaciones LIKE ? "
                              "ORDER BY fecha DESC LIMIT 4;", (like, like)):
            out.append({"tipo": "Venta", "titulo": v["concepto"],
                        "detalle": f"{v['fecha']} · ${money(v['total_cents'])}", "ir": "ventas"})
    if "catalogo" in p:
        for pr in conn.execute("SELECT * FROM products WHERE name LIKE ? LIMIT 4;", (like,)):
            out.append({"tipo": "Producto", "titulo": pr["name"], "detalle": f"${money(pr['price_cents'])}",
                        "ir": "catalogo"})
    if "inventario" in p:
        for i in conn.execute("SELECT * FROM insumos WHERE nombre LIKE ? LIMIT 4;", (like,)):
            out.append({"tipo": "Insumo", "titulo": i["nombre"], "detalle": f"Stock: {float(i['stock']):g} {i['unidad']}",
                        "ir": "inventario"})
    return ok(resultados=out)
