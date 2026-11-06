"""Usuarios, sesión y permisos.

Cada usuario tiene su propia lista de permisos (el rol es solo un punto de
partida). La pantalla muestra únicamente lo que el usuario puede hacer y el
servidor lo vuelve a controlar en cada pedido.

"Acceso libre": si la configuración lo indica, la app entra sola con un usuario
(por defecto «Caja») para que tomar pedidos no requiera contraseña, igual que
siempre. Las secciones de administración siguen pidiendo un usuario con permiso.
"""
from __future__ import annotations

import json
import sqlite3
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

from flask import g, jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import config
from .db import ahora_iso, get_db

# clave -> (etiqueta, grupo)
PERMISOS: Dict[str, Any] = {
    "pos": ("Tomar pedidos", "Operación"),
    "pedidos": ("Ver y gestionar pedidos", "Operación"),
    "pedidos_eliminar": ("Eliminar pedidos", "Operación"),
    "liquidacion": ("Ver la liquidación", "Operación"),
    "cierre": ("Cerrar la jornada", "Operación"),
    "inicio": ("Ver el inicio (indicadores)", "Finanzas"),
    "ventas": ("Ventas", "Finanzas"),
    "gastos": ("Gastos y pagos fijos", "Finanzas"),
    "reportes": ("Reportes y exportación", "Finanzas"),
    "inventario": ("Inventario y stock", "Stock"),
    "catalogo": ("Productos, promos e ingredientes", "Administración"),
    "repartidores": ("Repartidores", "Administración"),
    "usuarios": ("Usuarios y permisos", "Administración"),
    "configuracion": ("Configuración, respaldos e importación", "Administración"),
}

PRESETS: Dict[str, List[str]] = {
    "admin": list(PERMISOS.keys()),
    # Lo que hoy hace la caja sin contraseña: tomar pedidos, verlos, eliminarlos
    # y mirar la liquidación. Además puede cargar gastos (como en Caja).
    "cajero": ["pos", "pedidos", "pedidos_eliminar", "liquidacion", "gastos"],
}
ROLES = {"admin": "Administrador", "cajero": "Cajero"}

HASH_METODO = "pbkdf2:sha256:260000"
# Contraseña de administrador que la app traía escrita en el código (y por lo
# tanto pública). Se conserva solo para no dejar afuera al local al actualizar;
# mientras siga vigente, la pantalla le pide al administrador que la cambie.
CLAVE_DE_FABRICA = "blamewav"


def hashear(clave: str) -> str:
    return generate_password_hash(clave, method=HASH_METODO)


def clave_ok(hash_guardado: str, clave: str) -> bool:
    if not hash_guardado:
        return False
    try:
        return check_password_hash(hash_guardado, clave)
    except (ValueError, TypeError):
        return False


def permisos_de(fila: Any) -> List[str]:
    if fila["role"] == "admin":
        return list(PERMISOS.keys())
    try:
        lista = json.loads(fila["permissions"] or "[]")
    except ValueError:
        lista = []
    return [p for p in lista if p in PERMISOS]


def _usuario_publico(fila: Any, libre: bool = False) -> Dict[str, Any]:
    return {
        "id": int(fila["id"]),
        "username": fila["username"],
        "name": fila["name"] or fila["username"],
        "role": fila["role"],
        "role_label": ROLES.get(fila["role"], fila["role"]),
        "permisos": permisos_de(fila),
        "acceso_libre": libre,
        "tiene_clave": bool(fila["password_hash"]),
    }


def _buscar(conn: sqlite3.Connection, uid: Any) -> Optional[sqlite3.Row]:
    try:
        return conn.execute("SELECT * FROM users WHERE id=? AND active=1;", (int(uid),)).fetchone()
    except (TypeError, ValueError, sqlite3.Error):
        return None


def usuario_libre(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    uid = config.obtener(conn, "acceso_libre_usuario_id")
    return _buscar(conn, uid) if uid else None


def usuario_actual() -> Optional[Dict[str, Any]]:
    if "usuario" in g:
        return g.usuario
    conn = get_db()
    u = None
    uid = session.get("uid")
    if uid:
        fila = _buscar(conn, uid)
        if fila is not None:
            u = _usuario_publico(fila)
        else:
            session.pop("uid", None)
    if u is None and not session.get("salio"):
        fila = usuario_libre(conn)
        if fila is not None:
            u = _usuario_publico(fila, libre=True)
    g.usuario = u
    return u


def iniciar_sesion(conn: sqlite3.Connection, fila: Any) -> Dict[str, Any]:
    session.clear()
    session.permanent = True
    session["uid"] = int(fila["id"])
    conn.execute("UPDATE users SET last_login=? WHERE id=?;", (ahora_iso(), int(fila["id"])))
    conn.commit()
    g.pop("usuario", None)
    return _usuario_publico(fila)


def cerrar_sesion() -> None:
    session.clear()
    session.permanent = True
    session["salio"] = True
    g.pop("usuario", None)


def tiene(permiso: str) -> bool:
    u = usuario_actual()
    return bool(u and permiso in u["permisos"])


def requiere(*permisos: str) -> Callable:
    """Corta con 401 sin sesión y con 403 si falta el permiso (alcanza con uno)."""

    def deco(f: Callable) -> Callable:
        @wraps(f)
        def envuelta(*a: Any, **k: Any) -> Any:
            u = usuario_actual()
            if u is None:
                return jsonify({"ok": False, "login": True,
                                "error": "Tu sesión se cerró. Volvé a iniciar sesión."}), 401
            if permisos and not any(p in u["permisos"] for p in permisos):
                nombres = " o ".join(PERMISOS[p][0].lower() for p in permisos if p in PERMISOS)
                return jsonify({"ok": False, "error": f"Tu usuario no tiene permiso para {nombres}. "
                                                     f"Pedíselo a un administrador."}), 403
            return f(*a, **k)

        return envuelta

    return deco


def auditar(conn: sqlite3.Connection, accion: str, detalle: str = "") -> None:
    """Registro de lo sensible (borrar pedidos, cierres, restaurar…). Solo se agrega."""
    u = None
    try:
        u = usuario_actual()
    except RuntimeError:
        pass
    conn.execute(
        "INSERT INTO auditoria(fecha, user_id, user_name, accion, detalle) VALUES(?,?,?,?,?);",
        (ahora_iso(), u["id"] if u else None, u["name"] if u else "", accion, detalle[:500]),
    )


def usa_clave_de_fabrica(fila: Any) -> bool:
    return fila is not None and fila["role"] == "admin" and clave_ok(fila["password_hash"], CLAVE_DE_FABRICA)
