"""Base de datos: conexión, migraciones versionadas y respaldos.

La base vive donde diga MAGIK_DB_PATH (el launcher la pone en la carpeta
persistente del usuario, así ninguna actualización la pisa). Sin esa variable,
queda al lado de app.py, como siempre.

Las migraciones son versionadas con PRAGMA user_version y, además, cada una es
idempotente: las bases de los clientes vienen de versiones distintas de la app
y algunas ya tienen columnas que otras no. Antes de migrar una base con datos
se saca una copia en la carpeta de respaldos.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from flask import g

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ruta_db() -> str:
    return os.environ.get("MAGIK_DB_PATH") or os.path.join(APP_DIR, "magikburger.db")


def carpeta_datos() -> str:
    """Carpeta donde vive la base. Ahí van también reportes, respaldos y la marca."""
    return os.path.dirname(os.path.abspath(ruta_db()))


def carpeta(nombre: str) -> str:
    d = os.path.join(carpeta_datos(), nombre)
    os.makedirs(d, exist_ok=True)
    return d


def conectar(path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or ruta_db(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000;")
    return conn


def get_db() -> sqlite3.Connection:
    """Una conexión por request (se cierra sola al terminar)."""
    if "db" not in g:
        g.db = conectar()
    return g.db


def cerrar_db(_exc: Any = None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def tiene_columna(conn: sqlite3.Connection, tabla: str, col: str) -> bool:
    try:
        return any(r["name"] == col for r in conn.execute(f"PRAGMA table_info({tabla});").fetchall())
    except sqlite3.Error:
        return False


def tiene_tabla(conn: sqlite3.Connection, tabla: str) -> bool:
    r = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?;", (tabla,)
    ).fetchone()
    return r is not None


def _agregar_columna(conn: sqlite3.Connection, tabla: str, definicion: str) -> None:
    col = definicion.split()[0]
    if not tiene_columna(conn, tabla, col):
        conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {definicion};")


# ---------------------------------------------------------------------------
# Respaldos
# ---------------------------------------------------------------------------
def copiar_base(destino: str, origen: Optional[str] = None) -> None:
    """Copia consistente con la API de backup de SQLite (sirve con la base abierta)."""
    src = conectar(origen)
    try:
        dst = sqlite3.connect(destino)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def respaldo_automatico(motivo: str, conservar: int = 30) -> Optional[str]:
    """Deja una copia de la base en <datos>/respaldos y poda las viejas."""
    try:
        if not os.path.exists(ruta_db()):
            return None
        dir_resp = carpeta("respaldos")
        sello = datetime.now().strftime("%Y%m%d-%H%M%S")
        destino = os.path.join(dir_resp, f"{motivo}_{sello}.db")
        copiar_base(destino)
        autos = sorted(
            (f for f in os.listdir(dir_resp) if f.endswith(".db") and not f.startswith("pre-")),
            key=lambda f: os.path.getmtime(os.path.join(dir_resp, f)),
            reverse=True,
        )
        for viejo in autos[max(1, int(conservar)):]:
            try:
                os.remove(os.path.join(dir_resp, viejo))
            except OSError:
                pass
        return destino
    except Exception as e:  # un respaldo fallido nunca frena la operación
        print(f"[magik] no se pudo hacer el respaldo automático ({motivo}): {e}")
        return None


# ---------------------------------------------------------------------------
# Migraciones
# ---------------------------------------------------------------------------
def _m1_base(conn: sqlite3.Connection) -> None:
    """Esquema histórico de Magik (lo que hacía ensure_schema). Idempotente."""
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          price_cents INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS ingredients (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          extra_price_cents INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS product_base_ingredients (
          product_id INTEGER NOT NULL,
          ingredient_id INTEGER NOT NULL,
          PRIMARY KEY(product_id, ingredient_id)
        );
        CREATE TABLE IF NOT EXISTS couriers (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS orders (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'new',
          phone TEXT NOT NULL,
          address TEXT NOT NULL,
          courier_id INTEGER NULL,
          payment_method TEXT NOT NULL DEFAULT 'cash',
          total_cents INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS order_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER NOT NULL,
          product_id INTEGER NOT NULL,
          name_snapshot TEXT NOT NULL,
          base_price_cents INTEGER NOT NULL DEFAULT 0,
          qty INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS order_item_mods (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_item_id INTEGER NOT NULL,
          kind TEXT NOT NULL,
          ingredient_id INTEGER NOT NULL,
          name_snapshot TEXT NOT NULL,
          price_cents INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS promos (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          price_cents INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS promo_components (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          promo_id INTEGER NOT NULL,
          product_id INTEGER NOT NULL,
          sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS order_adjustments (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER NOT NULL,
          label TEXT NOT NULL,
          amount_cents INTEGER NOT NULL DEFAULT 0,
          sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT
        );
        """
    )
    _agregar_columna(conn, "couriers", "cash_float_cents INTEGER NOT NULL DEFAULT 150000")
    _agregar_columna(conn, "couriers", "active INTEGER NOT NULL DEFAULT 1")
    _agregar_columna(conn, "order_items", "product_name_snapshot TEXT")
    _agregar_columna(conn, "order_items", "product_price_cents_snapshot INTEGER NOT NULL DEFAULT 0")
    _agregar_columna(conn, "order_items", "promo_group TEXT")
    _agregar_columna(conn, "order_items", "promo_id INTEGER NOT NULL DEFAULT 0")
    _agregar_columna(conn, "order_items", "promo_name_snapshot TEXT")
    _agregar_columna(conn, "order_items", "promo_price_cents INTEGER NOT NULL DEFAULT 0")
    _agregar_columna(conn, "orders", "payment_method TEXT NOT NULL DEFAULT 'cash'")

    # El envío se marca con `kind` (antes se adivinaba por el texto del rótulo).
    if not tiene_columna(conn, "order_adjustments", "kind"):
        conn.execute("ALTER TABLE order_adjustments ADD COLUMN kind TEXT NOT NULL DEFAULT '';")
        from .dominio.pedidos import es_rotulo_de_envio

        for r in conn.execute("SELECT id, label FROM order_adjustments;").fetchall():
            if es_rotulo_de_envio(r["label"]):
                conn.execute("UPDATE order_adjustments SET kind='delivery' WHERE id=?;", (r["id"],))


def _m2_unificacion(conn: sqlite3.Connection) -> None:
    """Fusión con el panel financiero: historial, usuarios, finanzas, stock."""
    _agregar_columna(conn, "orders", "cierre_id INTEGER NULL")
    _agregar_columna(conn, "orders", "notes TEXT NOT NULL DEFAULT ''")
    _agregar_columna(conn, "orders", "created_by INTEGER NULL")
    _agregar_columna(conn, "products", "category TEXT NOT NULL DEFAULT ''")
    _agregar_columna(conn, "products", "sort_order INTEGER NOT NULL DEFAULT 0")

    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS ix_orders_cierre ON orders(cierre_id);
        CREATE INDEX IF NOT EXISTS ix_orders_created ON orders(created_at);
        CREATE INDEX IF NOT EXISTS ix_orders_phone ON orders(phone);
        CREATE INDEX IF NOT EXISTS ix_items_order ON order_items(order_id);
        CREATE INDEX IF NOT EXISTS ix_mods_item ON order_item_mods(order_item_id);
        CREATE INDEX IF NOT EXISTS ix_adj_order ON order_adjustments(order_id);

        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          name TEXT NOT NULL DEFAULT '',
          password_hash TEXT NOT NULL DEFAULT '',
          role TEXT NOT NULL DEFAULT 'cajero',
          permissions TEXT NOT NULL DEFAULT '[]',
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT '',
          last_login TEXT
        );

        CREATE TABLE IF NOT EXISTS cierres (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          jornada TEXT NOT NULL,
          created_at TEXT NOT NULL,
          user_id INTEGER,
          user_name TEXT NOT NULL DEFAULT '',
          orders_count INTEGER NOT NULL DEFAULT 0,
          gross_total_cents INTEGER NOT NULL DEFAULT 0,
          cash_to_render_cents INTEGER NOT NULL DEFAULT 0,
          other_methods_cents INTEGER NOT NULL DEFAULT 0,
          delivery_cents INTEGER NOT NULL DEFAULT 0,
          pdf_filename TEXT NOT NULL DEFAULT '',
          resumen_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS metodos_pago (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          is_cash INTEGER NOT NULL DEFAULT 0,
          hide_prices_ticket INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1,
          sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS gastos (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          fecha TEXT NOT NULL,
          categoria TEXT NOT NULL DEFAULT '',
          descripcion TEXT NOT NULL,
          proveedor TEXT NOT NULL DEFAULT '',
          importe_cents INTEGER NOT NULL DEFAULT 0,
          metodo_pago TEXT NOT NULL DEFAULT '',
          observaciones TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT '',
          user_id INTEGER,
          origen TEXT NOT NULL DEFAULT '',
          origen_id TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_gastos_fecha ON gastos(fecha);

        CREATE TABLE IF NOT EXISTS ventas_manuales (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          fecha TEXT NOT NULL,
          hora TEXT NOT NULL DEFAULT '',
          concepto TEXT NOT NULL,
          cantidad REAL NOT NULL DEFAULT 1,
          precio_unit_cents INTEGER NOT NULL DEFAULT 0,
          total_cents INTEGER NOT NULL DEFAULT 0,
          metodo_pago TEXT NOT NULL DEFAULT '',
          observaciones TEXT NOT NULL DEFAULT '',
          insumo_id INTEGER,
          created_at TEXT NOT NULL DEFAULT '',
          user_id INTEGER,
          origen TEXT NOT NULL DEFAULT '',
          origen_id TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_ventas_fecha ON ventas_manuales(fecha);

        CREATE TABLE IF NOT EXISTS insumos (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          nombre TEXT NOT NULL UNIQUE COLLATE NOCASE,
          categoria TEXT NOT NULL DEFAULT '',
          unidad TEXT NOT NULL DEFAULT 'unidades',
          stock REAL NOT NULL DEFAULT 0,
          stock_minimo REAL NOT NULL DEFAULT 0,
          costo_unit_cents INTEGER NOT NULL DEFAULT 0,
          activo INTEGER NOT NULL DEFAULT 1,
          origen TEXT NOT NULL DEFAULT '',
          origen_id TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS movimientos_stock (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          fecha TEXT NOT NULL,
          insumo_id INTEGER NOT NULL,
          cantidad REAL NOT NULL DEFAULT 0,
          tipo TEXT NOT NULL DEFAULT 'ajuste',
          motivo TEXT NOT NULL DEFAULT '',
          stock_resultante REAL,
          cierre_id INTEGER,
          user_id INTEGER,
          user_name TEXT NOT NULL DEFAULT '',
          origen TEXT NOT NULL DEFAULT '',
          origen_id TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_mov_insumo ON movimientos_stock(insumo_id);

        CREATE TABLE IF NOT EXISTS recetas (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          product_id INTEGER NOT NULL,
          insumo_id INTEGER NOT NULL,
          cantidad REAL NOT NULL DEFAULT 0,
          UNIQUE(product_id, insumo_id)
        );

        CREATE TABLE IF NOT EXISTS ingrediente_insumo (
          ingredient_id INTEGER PRIMARY KEY,
          insumo_id INTEGER NOT NULL,
          cantidad REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS recordatorios (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          servicio TEXT NOT NULL,
          dia INTEGER NOT NULL DEFAULT 1,
          monto_cents INTEGER NOT NULL DEFAULT 0,
          categoria TEXT NOT NULL DEFAULT '',
          activo INTEGER NOT NULL DEFAULT 1,
          ultimo_pago TEXT NOT NULL DEFAULT '',
          origen TEXT NOT NULL DEFAULT '',
          origen_id TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS importaciones (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          origen TEXT NOT NULL,
          archivo_hash TEXT NOT NULL,
          fecha TEXT NOT NULL,
          resumen_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS auditoria (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          fecha TEXT NOT NULL,
          user_id INTEGER,
          user_name TEXT NOT NULL DEFAULT '',
          accion TEXT NOT NULL,
          detalle TEXT NOT NULL DEFAULT ''
        );
        """
    )

    # Medios de pago: los dos que la app manejaba hasta hoy, con los mismos códigos
    # que ya están grabados en los pedidos ('cash' y 'transfer').
    if conn.execute("SELECT COUNT(*) FROM metodos_pago;").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO metodos_pago(code, name, is_cash, hide_prices_ticket, active, sort_order) "
            "VALUES('cash','Efectivo',1,0,1,0), ('transfer','Transferencia',0,1,1,1);"
        )

    # Usuarios: el administrador entra con la contraseña de admin que ya usaba el
    # local, y la caja queda con acceso libre para que tomar pedidos siga siendo
    # igual que siempre (sin pedir usuario).
    if conn.execute("SELECT COUNT(*) FROM users;").fetchone()[0] == 0:
        from .auth import CLAVE_DE_FABRICA, PRESETS, hashear

        ahora = datetime.now().isoformat(timespec="seconds")
        import json

        conn.execute(
            "INSERT INTO users(username, name, password_hash, role, permissions, active, created_at) "
            "VALUES(?,?,?,?,?,1,?);",
            ("admin", "Administrador", hashear(CLAVE_DE_FABRICA), "admin", json.dumps(PRESETS["admin"]), ahora),
        )
        cur = conn.execute(
            "INSERT INTO users(username, name, password_hash, role, permissions, active, created_at) "
            "VALUES(?,?,?,?,?,1,?);",
            ("caja", "Caja", "", "cajero", json.dumps(PRESETS["cajero"]), ahora),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES('acceso_libre_usuario_id', ?);",
            (str(cur.lastrowid),),
        )


def _m3_paga_con(conn: sqlite3.Connection) -> None:
    """P-014: con cuánto paga el cliente en efectivo, para imprimir el vuelto. Solo agrega una columna."""
    _agregar_columna(conn, "orders", "paga_con_cents INTEGER")


MIGRACIONES: List[Callable[[sqlite3.Connection], None]] = [_m1_base, _m2_unificacion, _m3_paga_con]
VERSION_ACTUAL = len(MIGRACIONES)


def _version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version;").fetchone()[0])


def _tiene_datos(conn: sqlite3.Connection) -> bool:
    for t in ("orders", "products", "gastos"):
        try:
            if tiene_tabla(conn, t) and conn.execute(f"SELECT 1 FROM {t} LIMIT 1;").fetchone():
                return True
        except sqlite3.Error:
            pass
    return False


def migrar(path: Optional[str] = None) -> int:
    """Lleva la base a la última versión. Devuelve la versión final."""
    destino = path or ruta_db()
    os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
    conn = conectar(destino)
    try:
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.Error:
            pass
        v = _version(conn)
        if v >= VERSION_ACTUAL:
            return v

        if _tiene_datos(conn) and path is None:
            try:
                sello = datetime.now().strftime("%Y%m%d-%H%M%S")
                copiar_base(os.path.join(carpeta("respaldos"), f"pre-migracion-v{v}_{sello}.db"))
            except Exception as e:
                print(f"[magik] aviso: no se pudo respaldar antes de migrar: {e}")

        # Cada migración es idempotente: si se corta a la mitad, la próxima vez
        # se vuelve a correr entera sin romper nada.
        for i in range(v, VERSION_ACTUAL):
            try:
                MIGRACIONES[i](conn)
                conn.execute(f"PRAGMA user_version = {i + 1};")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return _version(conn)
    finally:
        conn.close()


def ahora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def filas(rows: Any) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]

