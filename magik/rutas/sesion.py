"""Inicio de sesión y datos de la sesión actual."""
from __future__ import annotations

from flask import Blueprint, session

from .. import auth, config
from ..db import get_db
from ..dominio.liquidacion import jornada_actual
from .util import cuerpo, error, ok, texto

bp = Blueprint("sesion", __name__)


def _datos_sesion() -> dict:
    conn = get_db()
    cfg = config.todas(conn)
    libre = auth.usuario_libre(conn)
    u = auth.usuario_actual()
    return {
        "usuario": u,
        "hoy": jornada_actual(conn),
        "negocio": {"nombre": cfg["negocio_nombre"], "simbolo": cfg["moneda_simbolo"], "tema": cfg["tema"]},
        "acceso_libre": ({"id": int(libre["id"]), "name": libre["name"] or libre["username"]} if libre else None),
        "permisos": {k: {"label": v[0], "grupo": v[1]} for k, v in auth.PERMISOS.items()},
    }


@bp.get("/api/sesion")
def api_sesion():
    return ok(**_datos_sesion())


@bp.get("/api/login/usuarios")
def api_login_usuarios():
    """Nombres para elegir en la pantalla de ingreso (sin datos sensibles)."""
    filas = get_db().execute(
        "SELECT id, username, name, password_hash FROM users WHERE active=1 ORDER BY role='admin' DESC, name;").fetchall()
    return ok(usuarios=[{"id": int(f["id"]), "username": f["username"], "name": f["name"] or f["username"],
                         "tiene_clave": bool(f["password_hash"])} for f in filas])


@bp.post("/api/login")
def api_login():
    d = cuerpo()
    conn = get_db()
    usuario = texto(d, "username", 60)
    clave = str(d.get("password") or "")
    if not usuario:
        return error("Escribí tu usuario.")
    fila = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1;", (usuario,)).fetchone()
    if fila is None:
        return error("Usuario o contraseña incorrectos.", 401)
    if fila["password_hash"]:
        if not auth.clave_ok(fila["password_hash"], clave):
            return error("Usuario o contraseña incorrectos.", 401)
    else:
        # Sin contraseña solo entra el usuario de acceso libre (la caja).
        libre = auth.usuario_libre(conn)
        if libre is None or int(libre["id"]) != int(fila["id"]):
            return error("Este usuario todavía no tiene contraseña. Pedile a un administrador que le ponga una.", 401)
    u = auth.iniciar_sesion(conn, fila)
    return ok(usuario=u, **{k: v for k, v in _datos_sesion().items() if k != "usuario"})


@bp.post("/api/logout")
def api_logout():
    auth.cerrar_sesion()
    session.modified = True
    return ok()


@bp.get("/api/opciones")
@auth.requiere()
def api_opciones():
    """Listas que usan los formularios (medios de pago, categorías, unidades)."""
    conn = get_db()
    medios = [r["name"] for r in conn.execute("SELECT name FROM metodos_pago WHERE active=1 ORDER BY sort_order, id;")]
    return ok(medios=medios or ["Efectivo"], categorias_gasto=config.obtener(conn, "categorias_gasto"),
              unidades=config.obtener(conn, "unidades_stock"))


@bp.post("/api/mi-clave")
@auth.requiere()
def api_mi_clave():
    d = cuerpo()
    u = auth.usuario_actual()
    conn = get_db()
    fila = conn.execute("SELECT * FROM users WHERE id=?;", (u["id"],)).fetchone()
    if fila["password_hash"] and not auth.clave_ok(fila["password_hash"], str(d.get("actual") or "")):
        return error("La contraseña actual no es correcta.")
    nueva = str(d.get("nueva") or "")
    if len(nueva) < 4:
        return error("La contraseña nueva tiene que tener al menos 4 caracteres.")
    conn.execute("UPDATE users SET password_hash=? WHERE id=?;", (auth.hashear(nueva), u["id"]))
    auth.auditar(conn, "Cambio de contraseña propia", u["username"])
    conn.commit()
    return ok()
