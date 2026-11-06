"""Importa los datos de «Caja Hamburguesería» (su archivo caja.db) a Magik.

Se puede correr más de una vez: cada fila importada queda marcada con su origen
y su id original, así que lo que ya entró no se duplica.
Contraseñas de usuarios: Caja las guardaba en texto plano; acá se guardan
cifradas. Si el usuario ya existe en Magik, no se toca.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Dict

from . import config
from .auth import PRESETS, hashear
from .db import ahora_iso

ORIGEN = "caja"


def _cents(v: Any) -> int:
    try:
        return int(round(float(v or 0) * 100))
    except (TypeError, ValueError):
        return 0


def _ya(conn: sqlite3.Connection, tabla: str, oid: Any) -> bool:
    return conn.execute(f"SELECT 1 FROM {tabla} WHERE origen=? AND origen_id=? LIMIT 1;",
                        (ORIGEN, str(oid))).fetchone() is not None


def validar(ruta: str) -> sqlite3.Connection:
    try:
        src = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
        src.row_factory = sqlite3.Row
        tablas = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table';")}
    except sqlite3.Error:
        raise ValueError("El archivo no es una base de datos válida.")
    if not {"ventas", "gastos"} <= tablas:
        src.close()
        raise ValueError("Ese archivo no es una base de Caja Hamburguesería (le faltan las tablas de ventas y gastos). "
                         "Buscá el archivo caja.db en la carpeta de datos de Caja.")
    return src


def importar(conn: sqlite3.Connection, ruta: str, contenido: bytes) -> Dict[str, Any]:
    src = validar(ruta)
    tablas = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table';")}
    res = {"gastos": 0, "ventas": 0, "insumos": 0, "movimientos": 0, "recordatorios": 0, "usuarios": 0,
           "omitidos": 0}
    ahora = ahora_iso()
    try:
        cats = set(c.lower() for c in config.obtener(conn, "categorias_gasto"))
        nuevas_cats = []
        for g in src.execute("SELECT * FROM gastos;"):
            if _ya(conn, "gastos", g["id"]):
                res["omitidos"] += 1
                continue
            cat = (g["categoria"] or "").strip()
            if cat and cat.lower() not in cats:
                cats.add(cat.lower())
                nuevas_cats.append(cat)
            conn.execute(
                "INSERT INTO gastos(fecha, categoria, descripcion, proveedor, importe_cents, metodo_pago, observaciones, "
                "created_at, origen, origen_id) VALUES(?,?,?,?,?,?,?,?,?,?);",
                (str(g["fecha"])[:10], cat, g["descripcion"] or "(sin descripción)", g["proveedor"] or "",
                 _cents(g["importe"]), g["metodoPago"] or "", g["observaciones"] or "", ahora, ORIGEN, str(g["id"])))
            res["gastos"] += 1
        if nuevas_cats:
            config.guardar(conn, {"categorias_gasto": config.obtener(conn, "categorias_gasto") + nuevas_cats})

        for v in src.execute("SELECT * FROM ventas;"):
            if _ya(conn, "ventas_manuales", v["id"]):
                res["omitidos"] += 1
                continue
            conn.execute(
                "INSERT INTO ventas_manuales(fecha, hora, concepto, cantidad, precio_unit_cents, total_cents, metodo_pago, "
                "observaciones, created_at, origen, origen_id) VALUES(?,?,?,?,?,?,?,?,?,?,?);",
                (str(v["fecha"])[:10], v["hora"] or "", v["producto"] or "Venta", float(v["cantidad"] or 1),
                 _cents(v["precioUnitario"]), _cents(v["total"]), v["metodoPago"] or "", v["observaciones"] or "",
                 ahora, ORIGEN, str(v["id"])))
            res["ventas"] += 1

        mapa_insumos: Dict[int, int] = {}
        if "inventario" in tablas:
            for p in src.execute("SELECT * FROM inventario;"):
                existente = conn.execute("SELECT id FROM insumos WHERE nombre=? COLLATE NOCASE;",
                                         ((p["nombre"] or "").strip(),)).fetchone()
                if existente:
                    mapa_insumos[int(p["id"])] = int(existente["id"])
                    res["omitidos"] += 1
                    continue
                cur = conn.execute(
                    "INSERT INTO insumos(nombre, categoria, unidad, stock, stock_minimo, costo_unit_cents, origen, origen_id) "
                    "VALUES(?,?,?,?,?,?,?,?);",
                    ((p["nombre"] or "Insumo").strip(), p["categoria"] or "", p["unidad"] or "unidades",
                     float(p["stockActual"] or 0), float(p["stockMinimo"] or 0), _cents(p["costoUnitario"]),
                     ORIGEN, str(p["id"])))
                mapa_insumos[int(p["id"])] = int(cur.lastrowid)
                res["insumos"] += 1

        if "movimientos_stock" in tablas:
            for m in src.execute("SELECT * FROM movimientos_stock;"):
                destino = mapa_insumos.get(int(m["productoId"] or 0))
                if destino is None or _ya(conn, "movimientos_stock", m["id"]):
                    continue
                conn.execute(
                    "INSERT INTO movimientos_stock(fecha, insumo_id, cantidad, tipo, motivo, origen, origen_id) "
                    "VALUES(?,?,?,?,?,?,?);",
                    (str(m["fecha"]), destino, float(m["cantidad"] or 0), m["tipo"] or "ajuste",
                     (m["motivo"] or "") + " (importado de Caja)", ORIGEN, str(m["id"])))
                res["movimientos"] += 1

        if "recordatorios" in tablas:
            for r in src.execute("SELECT * FROM recordatorios;"):
                if _ya(conn, "recordatorios", r["id"]):
                    continue
                conn.execute(
                    "INSERT INTO recordatorios(servicio, dia, monto_cents, origen, origen_id) VALUES(?,?,?,?,?);",
                    (r["servicio"] or "Servicio", int(r["dia"] or 1), _cents(r["monto"]), ORIGEN, str(r["id"])))
                res["recordatorios"] += 1

        if "usuarios" in tablas:
            for u in src.execute("SELECT * FROM usuarios;"):
                nombre_usuario = (u["username"] or "").strip()
                if not nombre_usuario or conn.execute(
                        "SELECT 1 FROM users WHERE username=? COLLATE NOCASE;", (nombre_usuario,)).fetchone():
                    continue
                rol = "admin" if (u["role"] or "") == "admin" else "cajero"
                conn.execute(
                    "INSERT INTO users(username, name, password_hash, role, permissions, active, created_at) "
                    "VALUES(?,?,?,?,?,1,?);",
                    (nombre_usuario, u["nombre"] or nombre_usuario, hashear(u["password"]) if u["password"] else "", rol,
                     json.dumps(PRESETS[rol]), ahora))
                res["usuarios"] += 1

        if "config" in tablas:
            filas = {r["key"]: r["value"] for r in src.execute("SELECT key, value FROM config;")}
            if filas.get("umbralGasto") and config.obtener(conn, "umbral_gasto_cents") == config.REGISTRO["umbral_gasto_cents"][0]:
                config.guardar(conn, {"umbral_gasto_cents": _cents(filas["umbralGasto"])})

        conn.execute("INSERT INTO importaciones(origen, archivo_hash, fecha, resumen_json) VALUES(?,?,?,?);",
                     (ORIGEN, hashlib.sha256(contenido).hexdigest(), ahora, json.dumps(res)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        src.close()
    return res
