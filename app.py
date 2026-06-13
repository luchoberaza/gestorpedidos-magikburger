# (archivo completo)
from __future__ import annotations

import os
import sqlite3
import io
import sys
import importlib
import subprocess
from datetime import date
from flask import send_file
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
# La ruta de la base se puede fijar por variable de entorno (la usa el launcher para
# guardarla en una carpeta persistente del usuario y que las actualizaciones nunca la pisen).
# Si no está seteada, usa la de al lado del app.py (comportamiento de siempre).
DB_PATH = os.environ.get("MAGIK_DB_PATH") or os.path.join(APP_DIR, "magikburger.db")

ADMIN_PASSWORD = "blamewav"
SESSION_ADMIN_KEY = "magik_admin"

app = Flask(__name__)
app.secret_key = "magikburger-secret-key"


# -----------------------------
# DB helpers
# -----------------------------
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return any(r["name"] == col for r in rows)


def get_setting(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?;", (key,)).fetchone()
        return row["value"] if row and row["value"] is not None else default
    except sqlite3.Error:
        return default


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
        (key, str(value)),
    )


def ensure_schema() -> None:
    """
    Migraciones idempotentes. NO destruye datos: solo agrega columnas/tablas que falten.
    Se ejecuta al iniciar para que las bases de datos ya existentes del cliente se
    actualicen solas sin perder pedidos, productos ni configuraciones.
    """
    conn = db()
    try:
        cur = conn.cursor()

        # --- Cambio por repartidor (antes era una constante global fija de $1500) ---
        if not _table_has_column(conn, "couriers", "cash_float_cents"):
            cur.execute(
                f"ALTER TABLE couriers ADD COLUMN cash_float_cents INTEGER NOT NULL DEFAULT {CASH_FLOAT_CENTS};"
            )

        # --- Columnas de promo en order_items (agrupan los ítems de una misma promo) ---
        if not _table_has_column(conn, "order_items", "promo_group"):
            cur.execute("ALTER TABLE order_items ADD COLUMN promo_group TEXT;")
        if not _table_has_column(conn, "order_items", "promo_id"):
            cur.execute("ALTER TABLE order_items ADD COLUMN promo_id INTEGER NOT NULL DEFAULT 0;")
        if not _table_has_column(conn, "order_items", "promo_name_snapshot"):
            cur.execute("ALTER TABLE order_items ADD COLUMN promo_name_snapshot TEXT;")
        if not _table_has_column(conn, "order_items", "promo_price_cents"):
            cur.execute(
                "ALTER TABLE order_items ADD COLUMN promo_price_cents INTEGER NOT NULL DEFAULT 0;"
            )

        # --- Promos (combos de productos ya registrados, con precio propio) ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS promos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price_cents INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_components (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                promo_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (promo_id) REFERENCES promos(id) ON DELETE CASCADE,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            );
            """
        )

        # --- Ajustes de precio por pedido (envío, descuentos; pueden ser negativos) ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS order_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                label TEXT NOT NULL,
                amount_cents INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
            );
            """
        )

        # --- Configuración clave/valor (copias de ticket, envío por defecto, etc.) ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        cur.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('ticket_copies', '1');")
        cur.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('delivery_fee_cents', '0');")

        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    finally:
        conn.close()


def money(cents: int) -> int:
    return int(round((cents or 0) / 100))


def _parse_pesos_to_cents(value: Any, default_cents: int = 0, allow_negative: bool = False) -> int:
    """Convierte un texto en pesos ('1500', '1.500', '-200') a centavos enteros.
    Si no se puede parsear, devuelve default_cents. Limpia separadores de miles."""
    if value is None:
        return default_cents
    s = str(value).strip()
    if s == "":
        return default_cents
    # Quitamos símbolos de moneda y espacios; soportamos coma o punto decimal.
    s = s.replace("$", "").replace(" ", "")
    neg = s.startswith("-")
    s = s.lstrip("+-")
    # Heurística simple: si hay coma y punto, asumimos punto = miles, coma = decimal.
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        cents = int(round(float(s) * 100))
    except Exception:
        return default_cents
    if neg:
        cents = -cents
    if not allow_negative and cents < 0:
        cents = abs(cents)
    return cents


def _courier_float_cents(conn: sqlite3.Connection, cname: str) -> int:
    """Cambio (en centavos) que lleva un repartidor por su nombre.
    'Sin repartidor' no lleva cambio. Si la columna no existe, usa el valor por defecto."""
    if not cname or cname == "Sin repartidor":
        return 0
    try:
        if _table_has_column(conn, "couriers", "cash_float_cents"):
            row = conn.execute(
                "SELECT cash_float_cents FROM couriers WHERE name=? LIMIT 1;", (cname,)
            ).fetchone()
            if row and row["cash_float_cents"] is not None:
                return int(row["cash_float_cents"])
    except sqlite3.Error:
        pass
    return CASH_FLOAT_CENTS


def _apply_order_adjustments(conn: sqlite3.Connection, cur: sqlite3.Cursor, order_id: int, adjustments: Any) -> int:
    """Inserta los ajustes de un pedido (envío, descuentos) y devuelve la suma en
    centavos (puede ser negativa). Tolerante: ignora ajustes inválidos o en cero."""
    if not isinstance(adjustments, list):
        return 0
    if not _table_has_column(conn, "order_adjustments", "order_id"):
        return 0
    total = 0
    sort_i = 0
    for adj in adjustments:
        if not isinstance(adj, dict):
            continue
        label = (str(adj.get("label") or "").strip())[:60] or "Ajuste"
        try:
            amount = int(adj.get("amount_cents") or 0)
        except Exception:
            amount = 0
        if amount == 0:
            continue
        cur.execute(
            "INSERT INTO order_adjustments(order_id, label, amount_cents, sort_order) VALUES(?,?,?,?);",
            (order_id, label, amount, sort_i),
        )
        sort_i += 1
        total += amount
    return total


def _insert_order_items(conn: sqlite3.Connection, cur: sqlite3.Cursor, order_id: int, items: Any) -> int:
    """Inserta los ítems (y sus modificaciones) de un pedido y devuelve el total en
    centavos. Soporta promos: los ítems con promo_group toman el precio fijo de la promo
    (una sola vez por grupo, tomado de la tabla promos), ignorando el precio base de cada
    componente. Mantiene compatibilidad con columnas viejas/nuevas del schema."""
    prod_rows = conn.execute("SELECT id, name, price_cents FROM products WHERE active=1;").fetchall()
    prod_map = {int(p["id"]): p for p in prod_rows}
    ing_rows = conn.execute("SELECT id, name, extra_price_cents FROM ingredients WHERE active=1;").fetchall()
    ing_map = {int(i["id"]): i for i in ing_rows}

    has_name_old = _table_has_column(conn, "order_items", "name_snapshot")
    has_name_new = _table_has_column(conn, "order_items", "product_name_snapshot")
    has_price_old = _table_has_column(conn, "order_items", "base_price_cents")
    has_price_new = _table_has_column(conn, "order_items", "product_price_cents_snapshot")
    has_promo_cols = (
        _table_has_column(conn, "order_items", "promo_group")
        and _table_has_column(conn, "order_items", "promo_name_snapshot")
        and _table_has_column(conn, "order_items", "promo_price_cents")
    )
    has_promo_id = _table_has_column(conn, "order_items", "promo_id")

    promo_map = {}
    if _table_has_column(conn, "promos", "id"):
        for pr in conn.execute("SELECT id, name, price_cents FROM promos;").fetchall():
            promo_map[int(pr["id"])] = pr

    total_cents = 0
    promo_seen = set()

    for it in (items or []):
        pid = int(it.get("product_id") or 0)
        if pid not in prod_map:
            continue

        qty = int(it.get("qty") or 1)
        if qty < 1:
            qty = 1

        p = prod_map[pid]

        promo_group = (str(it.get("promo_group") or "").strip()) or None
        is_promo = bool(promo_group)
        promo_id = int(it.get("promo_id") or 0)
        promo = promo_map.get(promo_id)
        promo_name = promo["name"] if promo else str(it.get("promo_name") or "Promo")
        promo_price = int(promo["price_cents"]) if promo else int(it.get("promo_price_cents") or 0)

        # El precio de la promo se cuenta/guarda una sola vez por grupo (lo define el server).
        store_promo_price = 0
        if is_promo and promo_group not in promo_seen:
            store_promo_price = promo_price
            promo_seen.add(promo_group)

        # En promos el precio base del componente no cuenta (lo define la promo).
        base_price = 0 if is_promo else int(p["price_cents"])

        cols = ["order_id", "product_id", "qty"]
        vals: List[Any] = [order_id, pid, qty]

        if has_name_old:
            cols.append("name_snapshot"); vals.append(p["name"])
        if has_name_new:
            cols.append("product_name_snapshot"); vals.append(p["name"])
        if has_price_old:
            cols.append("base_price_cents"); vals.append(base_price)
        if has_price_new:
            cols.append("product_price_cents_snapshot"); vals.append(base_price)
        if has_promo_cols and is_promo:
            cols.append("promo_group"); vals.append(promo_group)
            cols.append("promo_name_snapshot"); vals.append(promo_name)
            cols.append("promo_price_cents"); vals.append(store_promo_price)
            if has_promo_id:
                cols.append("promo_id"); vals.append(promo_id)

        if not (has_name_old or has_name_new):
            raise sqlite3.OperationalError(
                "Schema incompatible: falta name_snapshot/product_name_snapshot en order_items."
            )

        cur.execute(
            f"INSERT INTO order_items({', '.join(cols)}) VALUES({', '.join(['?'] * len(cols))});",
            tuple(vals),
        )
        item_id = int(cur.lastrowid)

        removed_ids = it.get("removed_ingredient_ids") or []
        added_ids = it.get("added_ingredient_ids") or []

        for rid in removed_ids:
            rid = int(rid)
            if rid in ing_map:
                ing = ing_map[rid]
                cur.execute(
                    "INSERT INTO order_item_mods(order_item_id, kind, ingredient_id, name_snapshot, price_cents) "
                    "VALUES(?, 'remove', ?, ?, 0);",
                    (item_id, rid, ing["name"]),
                )

        add_total = 0
        for aid in added_ids:
            aid = int(aid)
            if aid in ing_map:
                ing = ing_map[aid]
                price = int(ing["extra_price_cents"])
                add_total += price
                cur.execute(
                    "INSERT INTO order_item_mods(order_item_id, kind, ingredient_id, name_snapshot, price_cents) "
                    "VALUES(?, 'add', ?, ?, ?);",
                    (item_id, aid, ing["name"], price),
                )

        item_total = (base_price + add_total) * qty + store_promo_price
        total_cents += item_total

    return total_cents


def fmt_dt(iso: str) -> Dict[str, str]:
    try:
        dt = datetime.fromisoformat(iso)
    except Exception:
        dt = datetime.now()
    return {"date": dt.strftime("%d/%m"), "time": dt.strftime("%H:%M")}

def ensure_reportlab():
    import importlib
    import subprocess
    import sys
    
    try:
        importlib.import_module("reportlab")
        return
    except ModuleNotFoundError:
        pass

    # Instalar con el python que está ejecutando este proceso
    cmd = [sys.executable, "-m", "pip", "install", "reportlab"]
    try:
        subprocess.check_call(cmd)
    except Exception as e:
        raise RuntimeError(
            "No se pudo instalar reportlab automáticamente. "
            f"Python usado por el launcher: {sys.executable}. Error: {e}"
        )

    # Reintentar import
    importlib.import_module("reportlab")


# -----------------------------
# Reporte diario (PDF)
# -----------------------------
CASH_FLOAT_CENTS = 1500 * 100  # $1500 para cambio (una vez por repartidor si apareció)


def _safe_iso_date_prefix() -> str:
    # "YYYY-MM-DD"
    return date.today().isoformat()


CASH_FLOAT_CENTS = 1500 * 100  # $1500 una sola vez por repartidor si tuvo pedidos


def generate_daily_orders_pdf_bytes(conn):
    ensure_reportlab()

    import io
    import os
    from datetime import date

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        KeepTogether,
    )

    # ---------- helpers schema ----------
    cols_orders = {r["name"] for r in conn.execute("PRAGMA table_info(orders);").fetchall()}
    cols_items = {r["name"] for r in conn.execute("PRAGMA table_info(order_items);").fetchall()}
    cols_mods = {r["name"] for r in conn.execute("PRAGMA table_info(order_item_mods);").fetchall()}

    has_payment = "payment_method" in cols_orders
    has_item_name_new = "product_name_snapshot" in cols_items
    has_item_name_old = "name_snapshot" in cols_items

    def is_transfer(pm: str) -> bool:
        pm = (pm or "").strip().lower()
        return pm.startswith("transf") or "transfer" in pm or "banco" in pm or "mp" in pm or "mercado" in pm

    def item_name(it) -> str:
        if has_item_name_new and it["product_name_snapshot"]:
            return str(it["product_name_snapshot"])
        if has_item_name_old and it["name_snapshot"]:
            return str(it["name_snapshot"])
        return "Producto"

    # ---------- fonts (Poppins) - registrar 1 sola vez ----------
    base_dir = os.path.dirname(os.path.abspath(__file__))

    reg_path = os.path.join(base_dir, "Poppins-Regular.ttf")
    sb_path = os.path.join(base_dir, "Poppins-SemiBold.ttf")
    b_path = os.path.join(base_dir, "Poppins-Bold.ttf")

    registered = set(pdfmetrics.getRegisteredFontNames())
    # No fallar si por alguna razón no se pueden cargar (pero en tu caso están)
    try:
        if "Poppins" not in registered:
            pdfmetrics.registerFont(TTFont("Poppins", reg_path))
        if "Poppins-SB" not in registered:
            pdfmetrics.registerFont(TTFont("Poppins-SB", sb_path))
        if "Poppins-B" not in registered:
            pdfmetrics.registerFont(TTFont("Poppins-B", b_path))
        FONT_REG = "Poppins"
        FONT_SB = "Poppins-SB"
        FONT_B = "Poppins-B"
    except Exception:
        FONT_REG = "Helvetica"
        FONT_SB = "Helvetica-Bold"
        FONT_B = "Helvetica-Bold"

    # ---------- data ----------
    today = date.today().isoformat()

    # Incluimos TODOS los pedidos vivos (no solo los de hoy): este reporte se genera justo
    # antes de "Limpiar pedidos", que borra todo. Si filtráramos por fecha, un pedido de
    # otro día se borraría sin quedar registrado en el PDF (pérdida de datos).
    orders = conn.execute(
        """
        SELECT o.*, c.name AS courier_name
        FROM orders o
        LEFT JOIN couriers c ON c.id = o.courier_id
        ORDER BY o.id ASC;
        """
    ).fetchall()

    order_ids = [int(o["id"]) for o in orders]
    items_by_order = {}
    mods_by_item = {}

    if order_ids:
        q = ",".join(["?"] * len(order_ids))
        items = conn.execute(
            f"""
            SELECT *
            FROM order_items
            WHERE order_id IN ({q})
            ORDER BY order_id ASC, id ASC;
            """,
            order_ids,
        ).fetchall()

        for it in items:
            items_by_order.setdefault(int(it["order_id"]), []).append(it)

        item_ids = [int(it["id"]) for it in items]
        if item_ids:
            q2 = ",".join(["?"] * len(item_ids))
            mods = conn.execute(
                f"""
                SELECT *
                FROM order_item_mods
                WHERE order_item_id IN ({q2})
                ORDER BY order_item_id ASC, id ASC;
                """,
                item_ids,
            ).fetchall()
            for m in mods:
                mods_by_item.setdefault(int(m["order_item_id"]), []).append(m)

    # ---------- cash summary ----------
    courier_seen = set()
    courier_cash_sales = {}  # solo ventas en efectivo (sin transfer)
    for o in orders:
        cname = (o["courier_name"] or "Sin repartidor").strip()
        courier_seen.add(cname)

        pm = (o["payment_method"] if has_payment else "cash") if has_payment else "cash"
        pm = pm or "cash"
        if not is_transfer(pm):
            courier_cash_sales[cname] = courier_cash_sales.get(cname, 0) + int(o["total_cents"] or 0)

    # ---------- palette (glass dark como tu UI) ----------
    BG = colors.HexColor("#0B0D15")
    CARD = colors.HexColor("#161A2B")
    GLASS = colors.HexColor("#1E223A")
    BORDER = colors.HexColor("#2B3052")
    ACCENT = colors.HexColor("#8B5CF6")
    ACCENT_SOFT = colors.HexColor("#A78BFA")
    TEXT = colors.HexColor("#E5E7EB")
    MUTED = colors.HexColor("#9CA3AF")

    # ---------- styles ----------
    s_title = ParagraphStyle("title", fontName=FONT_B, fontSize=20, leading=24, textColor=TEXT)
    s_sub = ParagraphStyle("sub", fontName=FONT_REG, fontSize=10, leading=14, textColor=MUTED)
    s_h = ParagraphStyle("h", fontName=FONT_SB, fontSize=12.5, leading=16, textColor=TEXT, spaceBefore=10, spaceAfter=6)
    s_txt = ParagraphStyle("txt", fontName=FONT_REG, fontSize=9.5, leading=14, textColor=TEXT)
    s_muted = ParagraphStyle("muted", fontName=FONT_REG, fontSize=9, leading=13, textColor=MUTED)
    s_kpi = ParagraphStyle("kpi", fontName=FONT_B, fontSize=11.5, leading=14, textColor=ACCENT_SOFT)

    # ---------- logo safe ----------
    logo_reader = None
    logo_path = os.path.join(base_dir, "logo.png")
    if os.path.exists(logo_path):
        try:
            logo_reader = ImageReader(logo_path)
        except Exception:
            logo_reader = None

    # ---------- background ----------
    def draw_bg(canvas, doc):
        w, h = A4
        canvas.saveState()
        canvas.setFillColor(BG)
        canvas.rect(0, 0, w, h, fill=1, stroke=0)

        # glow blobs
        canvas.setFillColor(colors.HexColor("#2A1F4F"))
        canvas.circle(w * 0.18, h * 0.90, 95, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#1F2A5F"))
        canvas.circle(w * 0.88, h * 0.80, 120, fill=1, stroke=0)

        # logo pequeño en header (si existe)
        if logo_reader is not None:
            try:
                canvas.drawImage(logo_reader, 16 * mm, h - 22 * mm, 12 * mm, 12 * mm, mask="auto", preserveAspectRatio=True)
            except Exception:
                pass

        canvas.restoreState()

    # ---------- build pdf ----------
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=22 * mm,
        bottomMargin=16 * mm,
        title=f"Reporte_{today}",
    )

    story = []

    # Header card
    header = Table(
        [[
            Paragraph("MagikBurger — Reporte diario", s_title),
            Paragraph(today, s_sub),
        ]],
        colWidths=[None, 40 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CARD),
                ("BOX", (0, 0), (-1, -1), 1, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 16),
                ("RIGHTPADDING", (0, 0), (-1, -1), 16),
                ("TOPPADDING", (0, 0), (-1, -1), 14),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
            ]
        )
    )
    story.append(header)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Pedidos del día", s_h))

    if not orders:
        empty = Table([[Paragraph("No hubo pedidos hoy.", s_txt)]], colWidths=[None])
        empty.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), GLASS),
                    ("BOX", (0, 0), (-1, -1), 1, BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 14),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                    ("TOPPADDING", (0, 0), (-1, -1), 12),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ]
            )
        )
        story.append(empty)
    else:
        for o in orders:
            oid = int(o["id"])
            dt = fmt_dt(o["created_at"])
            cname = (o["courier_name"] or "Sin repartidor").strip()
            pm = (o["payment_method"] if has_payment else "cash") if has_payment else "cash"
            pm = (pm or "cash").strip()
            total = money(int(o["total_cents"] or 0))

            phone = o["phone"] if "phone" in o.keys() else ""
            address = o["address"] if "address" in o.keys() else ""

            head = Paragraph(f"<b>#{oid}</b> · {dt['date']} {dt['time']} · <b>{cname}</b>", s_txt)
            meta = Paragraph(f"<font color='#9CA3AF'>Pago:</font> <b>{pm}</b> · <font color='#9CA3AF'>Total:</font> <b>${total}</b>", s_muted)
            contact = Paragraph(f"<font color='#9CA3AF'>Tel:</font> {phone} · <font color='#9CA3AF'>Dir:</font> {address}", s_muted)

            # Items
            item_rows = []
            for it in items_by_order.get(oid, []):
                qty = int(it["qty"] or 1)
                nm = item_name(it)

                iid = int(it["id"])
                mods_txt = []
                for md in mods_by_item.get(iid, []):
                    kind = (md["kind"] if "kind" in cols_mods else "") or ""
                    kind = kind.strip().lower()
                    nm2 = (md["name_snapshot"] if "name_snapshot" in cols_mods else "") or ""
                    pcents = int(md["price_cents"] or 0) if "price_cents" in cols_mods else 0

                    if kind == "remove":
                        mods_txt.append(f"sin {nm2}")
                    else:
                        mods_txt.append(f"+{nm2}" + (f" (+${money(pcents)})" if pcents else ""))

                mods_html = "<br/>".join(mods_txt) if mods_txt else "<font color='#9CA3AF'>—</font>"
                item_rows.append([
                    Paragraph(f"<b>{qty}×</b>", s_txt),
                    Paragraph(nm, s_txt),
                    Paragraph(mods_html, s_muted),
                ])

            if not item_rows:
                item_rows = [[Paragraph("—", s_muted), Paragraph("Sin ítems", s_muted), Paragraph("—", s_muted)]]

            items_tbl = Table(item_rows, colWidths=[12 * mm, 78 * mm, None], hAlign="LEFT")
            items_tbl.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 2),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#2B3052")),
                    ]
                )
            )

            # Card
            card = Table(
                [[head],
                 [meta],
                 [contact],
                 [Spacer(1, 2 * mm)],
                 [items_tbl]],
                colWidths=[None],
            )
            card.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), GLASS),
                        ("BOX", (0, 0), (-1, -1), 1, BORDER),
                        ("LEFTPADDING", (0, 0), (-1, -1), 14),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                        ("TOPPADDING", (0, 0), (-1, -1), 12),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                    ]
                )
            )

            story.append(KeepTogether([card, Spacer(1, 8)]))

    # Resumen efectivo
    story.append(Spacer(1, 6))
    story.append(Paragraph("Efectivo manejado por repartidor", s_h))
    story.append(Paragraph("Incluye el cambio configurado de cada repartidor si tuvo pedidos.", s_muted))
    story.append(Spacer(1, 6))

    if not courier_seen:
        story.append(Paragraph("No hubo repartidores con pedidos.", s_txt))
    else:
        rows = [[Paragraph("<b>Repartidor</b>", s_txt), Paragraph("<b>Efectivo</b>", s_txt)]]
        for cname in sorted(courier_seen):
            handled = courier_cash_sales.get(cname, 0) + _courier_float_cents(conn, cname)
            rows.append([Paragraph(cname, s_txt), Paragraph(f"<b>${money(handled)}</b>", s_kpi)])

        st = Table(rows, colWidths=[None, 42 * mm], hAlign="LEFT")
        st.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), CARD),
                    ("BOX", (0, 0), (-1, -1), 1, BORDER),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [GLASS, colors.HexColor("#202544")]),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        story.append(st)

    doc.build(story, onFirstPage=draw_bg, onLaterPages=draw_bg)

    pdf_bytes = buf.getvalue()
    buf.close()

    return f"Reporte_{today}.pdf", pdf_bytes

    ensure_reportlab()

    import io
    import os
    from datetime import date

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # ---------------- Font register (solo si no están) ----------------
    base_dir = os.path.dirname(os.path.abspath(__file__))
    reg_path = os.path.join(base_dir, "Poppins-Regular.ttf")
    sb_path = os.path.join(base_dir, "Poppins-SemiBold.ttf")
    b_path = os.path.join(base_dir, "Poppins-Bold.ttf")

    registered = set(pdfmetrics.getRegisteredFontNames())
    try:
        if "Poppins" not in registered:
            pdfmetrics.registerFont(TTFont("Poppins", reg_path))
        if "Poppins-SB" not in registered:
            pdfmetrics.registerFont(TTFont("Poppins-SB", sb_path))
        if "Poppins-B" not in registered:
            pdfmetrics.registerFont(TTFont("Poppins-B", b_path))
        FONT_R, FONT_SB, FONT_B = "Poppins", "Poppins-SB", "Poppins-B"
    except Exception:
        FONT_R, FONT_SB, FONT_B = "Helvetica", "Helvetica-Bold", "Helvetica-Bold"

    # ---------------- Data ----------------
    today = date.today().isoformat()

    cols_orders = {r["name"] for r in conn.execute("PRAGMA table_info(orders);").fetchall()}
    cols_items = {r["name"] for r in conn.execute("PRAGMA table_info(order_items);").fetchall()}
    cols_mods = {r["name"] for r in conn.execute("PRAGMA table_info(order_item_mods);").fetchall()}
    has_payment = "payment_method" in cols_orders
    has_item_name_new = "product_name_snapshot" in cols_items
    has_item_name_old = "name_snapshot" in cols_items

    orders = conn.execute(
        """
        SELECT o.*, c.name AS courier_name
        FROM orders o
        LEFT JOIN couriers c ON c.id = o.courier_id
        WHERE o.created_at LIKE ?
        ORDER BY o.id ASC;
        """,
        (f"{today}%",),
    ).fetchall()

    order_ids = [int(o["id"]) for o in orders]
    items_by_order = {}
    mods_by_item = {}

    if order_ids:
        q = ",".join(["?"] * len(order_ids))
        items = conn.execute(
            f"""
            SELECT *
            FROM order_items
            WHERE order_id IN ({q})
            ORDER BY order_id ASC, id ASC;
            """,
            order_ids,
        ).fetchall()

        for it in items:
            items_by_order.setdefault(int(it["order_id"]), []).append(it)

        item_ids = [int(it["id"]) for it in items]
        if item_ids:
            q2 = ",".join(["?"] * len(item_ids))
            mods = conn.execute(
                f"""
                SELECT *
                FROM order_item_mods
                WHERE order_item_id IN ({q2})
                ORDER BY order_item_id ASC, id ASC;
                """,
                item_ids,
            ).fetchall()

            for m in mods:
                mods_by_item.setdefault(int(m["order_item_id"]), []).append(m)

    def is_transfer(pm: str) -> bool:
        pm = (pm or "").strip().lower()
        return pm.startswith("transf") or "transfer" in pm or "banco" in pm or "mp" in pm or "mercado" in pm

    def item_name(it) -> str:
        if has_item_name_new and it["product_name_snapshot"]:
            return str(it["product_name_snapshot"])
        if has_item_name_old and it["name_snapshot"]:
            return str(it["name_snapshot"])
        return "Producto"

    # ---------------- Look (glass smooth) ----------------
    # (Ajustado para que NO se vea tosco: menos bordes, más aire, sombras suaves)
    C_BG = colors.HexColor("#070812")
    C_GLOW1 = colors.HexColor("#2A1F4F")
    C_GLOW2 = colors.HexColor("#1B2B6A")

    C_CARD = colors.HexColor("#12162A")
    C_CARD2 = colors.HexColor("#151B35")
    C_BORDER = colors.HexColor("#2A325A")

    C_TEXT = colors.HexColor("#EEF2FF")
    C_MUTED = colors.HexColor("#9AA4C3")

    C_ACCENT = colors.HexColor("#A78BFA")      # violeta suave
    C_ACCENT2 = colors.HexColor("#60A5FA")     # azul suave
    C_CHIP_BG = colors.HexColor("#1B2142")

    W, H = A4
    M = 14 * mm
    content_w = W - 2 * M

    # ---------------- Helpers dibujo ----------------
    def has_alpha(cnv):
        return hasattr(cnv, "setFillAlpha") and hasattr(cnv, "setStrokeAlpha")

    def set_alpha(cnv, a):
        if has_alpha(cnv):
            cnv.setFillAlpha(a)
            cnv.setStrokeAlpha(a)

    def draw_glow_bg(cnv):
        cnv.setFillColor(C_BG)
        cnv.rect(0, 0, W, H, fill=1, stroke=0)

        # glows suaves
        set_alpha(cnv, 0.55)
        cnv.setFillColor(C_GLOW1)
        cnv.circle(W * 0.15, H * 0.88, 110, fill=1, stroke=0)
        cnv.setFillColor(C_GLOW2)
        cnv.circle(W * 0.92, H * 0.78, 150, fill=1, stroke=0)
        set_alpha(cnv, 1.0)

    def rr(cnv, x, y, w, h, r=10, fill=1, stroke=0):
        cnv.roundRect(x, y, w, h, r, fill=fill, stroke=stroke)

    def card(cnv, x, y, w, h, r=14):
        # sombra soft
        set_alpha(cnv, 0.25)
        cnv.setFillColor(colors.black)
        rr(cnv, x + 2, y - 2, w, h, r=r, fill=1, stroke=0)
        set_alpha(cnv, 1.0)

        # cuerpo
        cnv.setFillColor(C_CARD)
        rr(cnv, x, y, w, h, r=r, fill=1, stroke=0)

        # borde sutil
        set_alpha(cnv, 0.55)
        cnv.setStrokeColor(C_BORDER)
        cnv.setLineWidth(0.8)
        rr(cnv, x, y, w, h, r=r, fill=0, stroke=1)
        set_alpha(cnv, 1.0)

    def chip(cnv, x, y, text, bg=C_CHIP_BG, fg=C_TEXT, pad_x=8, pad_y=5, font=FONT_SB, size=9):
        cnv.setFont(font, size)
        tw = pdfmetrics.stringWidth(text, font, size)
        w = tw + pad_x * 2
        h = size + pad_y * 2
        set_alpha(cnv, 0.95)
        cnv.setFillColor(bg)
        rr(cnv, x, y, w, h, r=8, fill=1, stroke=0)
        set_alpha(cnv, 1.0)
        cnv.setFillColor(fg)
        cnv.drawString(x + pad_x, y + pad_y, text)
        return w, h

    def wrap_lines(text, font, size, max_w):
        # wrap simple por palabras (suficiente para direcciones largas)
        words = str(text or "").split()
        if not words:
            return [""]
        lines = []
        cur = words[0]
        for w in words[1:]:
            test = cur + " " + w
            if pdfmetrics.stringWidth(test, font, size) <= max_w:
                cur = test
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        return lines

    # ---------------- Logo ----------------
    logo_reader = None
    logo_path = os.path.join(base_dir, "logo.png")
    if os.path.exists(logo_path):
        try:
            logo_reader = ImageReader(logo_path)
        except Exception:
            logo_reader = None

    # ---------------- Build PDF ----------------
    buf = io.BytesIO()
    cnv = canvas.Canvas(buf, pagesize=A4)

    def new_page():
        cnv.showPage()
        draw_glow_bg(cnv)

    draw_glow_bg(cnv)

    y = H - M

    # Header card (más “UI”)
    header_h = 24 * mm
    y -= header_h
    card(cnv, M, y, content_w, header_h, r=16)

    # Logo en “pill”
    if logo_reader is not None:
        # pill
        set_alpha(cnv, 0.9)
        cnv.setFillColor(C_CARD2)
        rr(cnv, M + 10, y + 6, 18 * mm, 18 * mm, r=10, fill=1, stroke=0)
        set_alpha(cnv, 1.0)
        try:
            cnv.drawImage(logo_reader, M + 12, y + 8, 14 * mm, 14 * mm, mask="auto", preserveAspectRatio=True)
        except Exception:
            pass

        title_x = M + 10 + 18 * mm + 10
    else:
        title_x = M + 12

    cnv.setFillColor(C_TEXT)
    cnv.setFont(FONT_B, 18)
    cnv.drawString(title_x, y + 15, "MagikBurger — Reporte diario")

    cnv.setFillColor(C_MUTED)
    cnv.setFont(FONT_R, 10)
    cnv.drawRightString(M + content_w - 12, y + 15.5, today)

    y -= 10 * mm

    # Sección
    cnv.setFillColor(C_TEXT)
    cnv.setFont(FONT_SB, 12)
    cnv.drawString(M, y, "Pedidos del día")
    y -= 7 * mm

    # Render cards de pedidos
    if not orders:
        h = 18 * mm
        y -= h
        card(cnv, M, y, content_w, h, r=16)
        cnv.setFillColor(C_TEXT)
        cnv.setFont(FONT_R, 11)
        cnv.drawString(M + 12, y + 10, "No hubo pedidos hoy.")
        y -= 10 * mm
    else:
        for o in orders:
            oid = int(o["id"])
            dt = fmt_dt(o["created_at"])
            cname = (o["courier_name"] or "Sin repartidor").strip()
            pm = (o["payment_method"] if has_payment else "cash") if has_payment else "cash"
            pm = (pm or "cash").strip()
            total = money(int(o["total_cents"] or 0))

            phone = o["phone"] if "phone" in o.keys() else ""
            address = o["address"] if "address" in o.keys() else ""

            # Calcular altura dinámica según items + wraps
            items = items_by_order.get(oid, [])
            lines_addr = wrap_lines(f"Dir: {address}", FONT_R, 9, content_w - 24)
            base_h = 30 * mm  # header interno + chips + contacto
            items_h = max(1, len(items)) * 6 * mm
            extra_addr_h = max(0, (len(lines_addr) - 1)) * 4 * mm
            card_h = base_h + items_h + extra_addr_h

            if y - card_h < 22 * mm:
                new_page()
                y = H - M
                # repetir título de sección arriba (queda pro)
                cnv.setFillColor(C_TEXT)
                cnv.setFont(FONT_SB, 12)
                cnv.drawString(M, y, "Pedidos del día")
                y -= 7 * mm

            y -= card_h
            card(cnv, M, y, content_w, card_h, r=18)

            # Header interno
            cnv.setFillColor(C_TEXT)
            cnv.setFont(FONT_SB, 11)
            cnv.drawString(M + 14, y + card_h - 16, f"#{oid} · {dt['date']} {dt['time']} · {cname}")

            # Chips (pago / total)
            chip_y = y + card_h - 28
            chip_x = M + 14
            chip(cnv, chip_x, chip_y, f"Pago: {pm}", bg=C_CHIP_BG, fg=C_MUTED, font=FONT_SB, size=9)
            chip(cnv, M + content_w - 14 - 60, chip_y, f"Total: ${total}", bg=colors.HexColor("#1C1630"), fg=C_ACCENT, font=FONT_B, size=9)

            # Contacto (wrap suave)
            cnv.setFillColor(C_MUTED)
            cnv.setFont(FONT_R, 9)
            cnv.drawString(M + 14, y + card_h - 42, f"Tel: {phone}")
            # address multiline
            ay = y + card_h - 54
            for ln in lines_addr:
                cnv.drawString(M + 14, ay, ln)
                ay -= 11

            # Divider soft
            set_alpha(cnv, 0.35)
            cnv.setStrokeColor(C_BORDER)
            cnv.setLineWidth(1)
            cnv.line(M + 14, ay - 6, M + content_w - 14, ay - 6)
            set_alpha(cnv, 1.0)

            # Items
            iy = ay - 18
            for it in items:
                qty = int(it["qty"] or 1)
                nm = item_name(it)

                cnv.setFillColor(C_TEXT)
                cnv.setFont(FONT_SB, 10)
                cnv.drawString(M + 14, iy, f"{qty}×")

                cnv.setFont(FONT_R, 10)
                cnv.drawString(M + 32, iy, nm)

                # mods (si hay)
                iid = int(it["id"])
                mods = mods_by_item.get(iid, [])
                if mods:
                    mod_lines = []
                    for md in mods:
                        kind = (md["kind"] if "kind" in cols_mods else "") or ""
                        kind = kind.strip().lower()
                        nm2 = (md["name_snapshot"] if "name_snapshot" in cols_mods else "") or ""
                        pcents = int(md["price_cents"] or 0) if "price_cents" in cols_mods else 0
                        if kind == "remove":
                            mod_lines.append(f"sin {nm2}")
                        else:
                            mod_lines.append(f"+{nm2}" + (f" (+${money(pcents)})" if pcents else ""))

                    if mod_lines:
                        cnv.setFillColor(C_MUTED)
                        cnv.setFont(FONT_R, 8.8)
                        mx = M + 32
                        my = iy - 10
                        # 1-2 líneas máx para que no se haga enorme
                        joined = " · ".join(mod_lines)
                        for ln in wrap_lines(joined, FONT_R, 8.8, content_w - 46):
                            cnv.drawString(mx, my, ln)
                            my -= 10
                        iy = min(iy - 18, my + 8)

                iy -= 18

            y -= 10 * mm  # espacio entre cards

    # (Resumen lo dejamos igual que ya te funciona en otra parte, si querés lo estilizo después)
    # Por ahora mantenemos este PDF solo para "Pedidos del día" con look perfecto.
    cnv.save()

    pdf_bytes = buf.getvalue()
    buf.close()

    return f"Reporte_{today}.pdf", pdf_bytes

    ensure_reportlab()

    import io
    import os
    from datetime import date

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        KeepTogether,
    )

    # ---------- helpers schema ----------
    cols_orders = {r["name"] for r in conn.execute("PRAGMA table_info(orders);").fetchall()}
    cols_items = {r["name"] for r in conn.execute("PRAGMA table_info(order_items);").fetchall()}
    cols_mods = {r["name"] for r in conn.execute("PRAGMA table_info(order_item_mods);").fetchall()}

    has_payment = "payment_method" in cols_orders
    has_item_name_new = "product_name_snapshot" in cols_items
    has_item_name_old = "name_snapshot" in cols_items

    def is_transfer(pm: str) -> bool:
        pm = (pm or "").strip().lower()
        return pm.startswith("transf") or "transfer" in pm or "banco" in pm or "mp" in pm or "mercado" in pm

    def item_name(it) -> str:
        if has_item_name_new and it["product_name_snapshot"]:
            return str(it["product_name_snapshot"])
        if has_item_name_old and it["name_snapshot"]:
            return str(it["name_snapshot"])
        return "Producto"

    # ---------- fonts (Poppins) - registrar 1 sola vez ----------
    base_dir = os.path.dirname(os.path.abspath(__file__))

    reg_path = os.path.join(base_dir, "Poppins-Regular.ttf")
    sb_path = os.path.join(base_dir, "Poppins-SemiBold.ttf")
    b_path = os.path.join(base_dir, "Poppins-Bold.ttf")

    registered = set(pdfmetrics.getRegisteredFontNames())
    # No fallar si por alguna razón no se pueden cargar (pero en tu caso están)
    try:
        if "Poppins" not in registered:
            pdfmetrics.registerFont(TTFont("Poppins", reg_path))
        if "Poppins-SB" not in registered:
            pdfmetrics.registerFont(TTFont("Poppins-SB", sb_path))
        if "Poppins-B" not in registered:
            pdfmetrics.registerFont(TTFont("Poppins-B", b_path))
        FONT_REG = "Poppins"
        FONT_SB = "Poppins-SB"
        FONT_B = "Poppins-B"
    except Exception:
        FONT_REG = "Helvetica"
        FONT_SB = "Helvetica-Bold"
        FONT_B = "Helvetica-Bold"

    # ---------- data ----------
    today = date.today().isoformat()

    # Incluimos TODOS los pedidos vivos (no solo los de hoy): este reporte se genera justo
    # antes de "Limpiar pedidos", que borra todo. Si filtráramos por fecha, un pedido de
    # otro día se borraría sin quedar registrado en el PDF (pérdida de datos).
    orders = conn.execute(
        """
        SELECT o.*, c.name AS courier_name
        FROM orders o
        LEFT JOIN couriers c ON c.id = o.courier_id
        ORDER BY o.id ASC;
        """
    ).fetchall()

    order_ids = [int(o["id"]) for o in orders]
    items_by_order = {}
    mods_by_item = {}

    if order_ids:
        q = ",".join(["?"] * len(order_ids))
        items = conn.execute(
            f"""
            SELECT *
            FROM order_items
            WHERE order_id IN ({q})
            ORDER BY order_id ASC, id ASC;
            """,
            order_ids,
        ).fetchall()

        for it in items:
            items_by_order.setdefault(int(it["order_id"]), []).append(it)

        item_ids = [int(it["id"]) for it in items]
        if item_ids:
            q2 = ",".join(["?"] * len(item_ids))
            mods = conn.execute(
                f"""
                SELECT *
                FROM order_item_mods
                WHERE order_item_id IN ({q2})
                ORDER BY order_item_id ASC, id ASC;
                """,
                item_ids,
            ).fetchall()
            for m in mods:
                mods_by_item.setdefault(int(m["order_item_id"]), []).append(m)

    # ---------- cash summary ----------
    courier_seen = set()
    courier_cash_sales = {}  # solo ventas en efectivo (sin transfer)
    for o in orders:
        cname = (o["courier_name"] or "Sin repartidor").strip()
        courier_seen.add(cname)

        pm = (o["payment_method"] if has_payment else "cash") if has_payment else "cash"
        pm = pm or "cash"
        if not is_transfer(pm):
            courier_cash_sales[cname] = courier_cash_sales.get(cname, 0) + int(o["total_cents"] or 0)

    # ---------- palette (glass dark como tu UI) ----------
    BG = colors.HexColor("#0B0D15")
    CARD = colors.HexColor("#161A2B")
    GLASS = colors.HexColor("#1E223A")
    BORDER = colors.HexColor("#2B3052")
    ACCENT = colors.HexColor("#8B5CF6")
    ACCENT_SOFT = colors.HexColor("#A78BFA")
    TEXT = colors.HexColor("#E5E7EB")
    MUTED = colors.HexColor("#9CA3AF")

    # ---------- styles ----------
    s_title = ParagraphStyle("title", fontName=FONT_B, fontSize=20, leading=24, textColor=TEXT)
    s_sub = ParagraphStyle("sub", fontName=FONT_REG, fontSize=10, leading=14, textColor=MUTED)
    s_h = ParagraphStyle("h", fontName=FONT_SB, fontSize=12.5, leading=16, textColor=TEXT, spaceBefore=10, spaceAfter=6)
    s_txt = ParagraphStyle("txt", fontName=FONT_REG, fontSize=9.5, leading=14, textColor=TEXT)
    s_muted = ParagraphStyle("muted", fontName=FONT_REG, fontSize=9, leading=13, textColor=MUTED)
    s_kpi = ParagraphStyle("kpi", fontName=FONT_B, fontSize=11.5, leading=14, textColor=ACCENT_SOFT)

    # ---------- logo safe ----------
    logo_reader = None
    logo_path = os.path.join(base_dir, "logo.png")
    if os.path.exists(logo_path):
        try:
            logo_reader = ImageReader(logo_path)
        except Exception:
            logo_reader = None

    # ---------- background ----------
    def draw_bg(canvas, doc):
        w, h = A4
        canvas.saveState()
        canvas.setFillColor(BG)
        canvas.rect(0, 0, w, h, fill=1, stroke=0)

        # glow blobs
        canvas.setFillColor(colors.HexColor("#2A1F4F"))
        canvas.circle(w * 0.18, h * 0.90, 95, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#1F2A5F"))
        canvas.circle(w * 0.88, h * 0.80, 120, fill=1, stroke=0)

        # logo pequeño en header (si existe)
        if logo_reader is not None:
            try:
                canvas.drawImage(logo_reader, 16 * mm, h - 22 * mm, 12 * mm, 12 * mm, mask="auto", preserveAspectRatio=True)
            except Exception:
                pass

        canvas.restoreState()

    # ---------- build pdf ----------
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=22 * mm,
        bottomMargin=16 * mm,
        title=f"Reporte_{today}",
    )

    story = []

    # Header card
    header = Table(
        [[
            Paragraph("MagikBurger — Reporte diario", s_title),
            Paragraph(today, s_sub),
        ]],
        colWidths=[None, 40 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CARD),
                ("BOX", (0, 0), (-1, -1), 1, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 16),
                ("RIGHTPADDING", (0, 0), (-1, -1), 16),
                ("TOPPADDING", (0, 0), (-1, -1), 14),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
            ]
        )
    )
    story.append(header)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Pedidos del día", s_h))

    if not orders:
        empty = Table([[Paragraph("No hubo pedidos hoy.", s_txt)]], colWidths=[None])
        empty.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), GLASS),
                    ("BOX", (0, 0), (-1, -1), 1, BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 14),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                    ("TOPPADDING", (0, 0), (-1, -1), 12),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ]
            )
        )
        story.append(empty)
    else:
        for o in orders:
            oid = int(o["id"])
            dt = fmt_dt(o["created_at"])
            cname = (o["courier_name"] or "Sin repartidor").strip()
            pm = (o["payment_method"] if has_payment else "cash") if has_payment else "cash"
            pm = (pm or "cash").strip()
            total = money(int(o["total_cents"] or 0))

            phone = o["phone"] if "phone" in o.keys() else ""
            address = o["address"] if "address" in o.keys() else ""

            head = Paragraph(f"<b>#{oid}</b> · {dt['date']} {dt['time']} · <b>{cname}</b>", s_txt)
            meta = Paragraph(f"<font color='#9CA3AF'>Pago:</font> <b>{pm}</b> · <font color='#9CA3AF'>Total:</font> <b>${total}</b>", s_muted)
            contact = Paragraph(f"<font color='#9CA3AF'>Tel:</font> {phone} · <font color='#9CA3AF'>Dir:</font> {address}", s_muted)

            # Items
            item_rows = []
            for it in items_by_order.get(oid, []):
                qty = int(it["qty"] or 1)
                nm = item_name(it)

                iid = int(it["id"])
                mods_txt = []
                for md in mods_by_item.get(iid, []):
                    kind = (md["kind"] if "kind" in cols_mods else "") or ""
                    kind = kind.strip().lower()
                    nm2 = (md["name_snapshot"] if "name_snapshot" in cols_mods else "") or ""
                    pcents = int(md["price_cents"] or 0) if "price_cents" in cols_mods else 0

                    if kind == "remove":
                        mods_txt.append(f"sin {nm2}")
                    else:
                        mods_txt.append(f"+{nm2}" + (f" (+${money(pcents)})" if pcents else ""))

                mods_html = "<br/>".join(mods_txt) if mods_txt else "<font color='#9CA3AF'>—</font>"
                item_rows.append([
                    Paragraph(f"<b>{qty}×</b>", s_txt),
                    Paragraph(nm, s_txt),
                    Paragraph(mods_html, s_muted),
                ])

            if not item_rows:
                item_rows = [[Paragraph("—", s_muted), Paragraph("Sin ítems", s_muted), Paragraph("—", s_muted)]]

            items_tbl = Table(item_rows, colWidths=[12 * mm, 78 * mm, None], hAlign="LEFT")
            items_tbl.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 2),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#2B3052")),
                    ]
                )
            )

            # Card
            card = Table(
                [[head],
                 [meta],
                 [contact],
                 [Spacer(1, 2 * mm)],
                 [items_tbl]],
                colWidths=[None],
            )
            card.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), GLASS),
                        ("BOX", (0, 0), (-1, -1), 1, BORDER),
                        ("LEFTPADDING", (0, 0), (-1, -1), 14),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                        ("TOPPADDING", (0, 0), (-1, -1), 12),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                    ]
                )
            )

            story.append(KeepTogether([card, Spacer(1, 8)]))

    # Resumen efectivo
    story.append(Spacer(1, 6))
    story.append(Paragraph("Efectivo manejado por repartidor", s_h))
    story.append(Paragraph("Incluye el cambio configurado de cada repartidor si tuvo pedidos.", s_muted))
    story.append(Spacer(1, 6))

    if not courier_seen:
        story.append(Paragraph("No hubo repartidores con pedidos.", s_txt))
    else:
        rows = [[Paragraph("<b>Repartidor</b>", s_txt), Paragraph("<b>Efectivo</b>", s_txt)]]
        for cname in sorted(courier_seen):
            handled = courier_cash_sales.get(cname, 0) + _courier_float_cents(conn, cname)
            rows.append([Paragraph(cname, s_txt), Paragraph(f"<b>${money(handled)}</b>", s_kpi)])

        st = Table(rows, colWidths=[None, 42 * mm], hAlign="LEFT")
        st.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), CARD),
                    ("BOX", (0, 0), (-1, -1), 1, BORDER),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [GLASS, colors.HexColor("#202544")]),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        story.append(st)

    doc.build(story, onFirstPage=draw_bg, onLaterPages=draw_bg)

    pdf_bytes = buf.getvalue()
    buf.close()

    return f"Reporte_{today}.pdf", pdf_bytes

    ensure_reportlab()

    import io
    import os
    from datetime import date

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        KeepTogether,
    )

    # ---------------- Fonts (Poppins) ----------------
    base_dir = os.path.dirname(os.path.abspath(__file__))
    pdfmetrics.registerFont(TTFont("Poppins", os.path.join(base_dir, "Poppins-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("Poppins-SB", os.path.join(base_dir, "Poppins-SemiBold.ttf")))
    pdfmetrics.registerFont(TTFont("Poppins-B", os.path.join(base_dir, "Poppins-Bold.ttf")))

    # ---------------- Data ----------------
    today = date.today().isoformat()

    orders = conn.execute(
        """
        SELECT o.*, c.name AS courier_name
        FROM orders o
        LEFT JOIN couriers c ON c.id = o.courier_id
        WHERE o.created_at LIKE ?
        ORDER BY o.id ASC;
        """,
        (f"{today}%",),
    ).fetchall()

    # ---------------- Palette (igual a tu UI) ----------------
    BG = colors.HexColor("#0B0D15")
    CARD = colors.HexColor("#161A2B")
    GLASS = colors.HexColor("#1E223A")
    BORDER = colors.HexColor("#2B3052")
    ACCENT = colors.HexColor("#8B5CF6")
    ACCENT_SOFT = colors.HexColor("#A78BFA")
    TEXT = colors.HexColor("#E5E7EB")
    MUTED = colors.HexColor("#9CA3AF")

    # ---------------- Styles ----------------
    s_title = ParagraphStyle(
        "title",
        fontName="Poppins-B",
        fontSize=22,
        textColor=TEXT,
        leading=26,
    )
    s_sub = ParagraphStyle(
        "sub",
        fontName="Poppins",
        fontSize=10,
        textColor=MUTED,
        leading=14,
    )
    s_h = ParagraphStyle(
        "h",
        fontName="Poppins-SB",
        fontSize=13,
        textColor=TEXT,
        spaceBefore=12,
        spaceAfter=6,
    )
    s_txt = ParagraphStyle(
        "txt",
        fontName="Poppins",
        fontSize=9.5,
        textColor=TEXT,
        leading=14,
    )
    s_muted = ParagraphStyle(
        "muted",
        fontName="Poppins",
        fontSize=9,
        textColor=MUTED,
        leading=13,
    )
    s_kpi = ParagraphStyle(
        "kpi",
        fontName="Poppins-B",
        fontSize=12,
        textColor=ACCENT_SOFT,
    )

    # ---------------- PDF ----------------
    buf = io.BytesIO()

    def draw_bg(canvas, doc):
        w, h = A4
        canvas.saveState()
        canvas.setFillColor(BG)
        canvas.rect(0, 0, w, h, fill=1, stroke=0)

        # glow blobs
        canvas.setFillColor(colors.HexColor("#2A1F4F"))
        canvas.circle(w * 0.15, h * 0.9, 90, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#1F2A5F"))
        canvas.circle(w * 0.85, h * 0.8, 110, fill=1, stroke=0)

        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=28 * mm,
        bottomMargin=18 * mm,
    )

    story = []

    # ---------------- Header card ----------------
    logo_path = os.path.join(base_dir, "logo.png")
    logo = ImageReader(logo_path) if os.path.exists(logo_path) else None

    header = Table(
        [[
            Paragraph("MagikBurger", s_title),
            Paragraph(today, s_sub),
        ]],
        colWidths=[None, 40 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CARD),
                ("BOX", (0, 0), (-1, -1), 1, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 16),
                ("RIGHTPADDING", (0, 0), (-1, -1), 16),
                ("TOPPADDING", (0, 0), (-1, -1), 14),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
            ]
        )
    )

    story.append(header)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Pedidos del día", s_h))

    if not orders:
        story.append(Paragraph("No hubo pedidos hoy.", s_txt))
    else:
        for o in orders:
            card = Table(
                [[
                    Paragraph(f"<b>#{o['id']}</b> · {o['courier_name']}", s_txt),
                    Paragraph(f"${money(o['total_cents'])}", s_kpi),
                ]],
                colWidths=[None, 30 * mm],
            )
            card.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), GLASS),
                        ("BOX", (0, 0), (-1, -1), 1, BORDER),
                        ("LEFTPADDING", (0, 0), (-1, -1), 14),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                        ("TOPPADDING", (0, 0), (-1, -1), 12),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                    ]
                )
            )

            story.append(KeepTogether([card, Spacer(1, 8)]))

    doc.build(story, onFirstPage=draw_bg, onLaterPages=draw_bg)

    pdf_bytes = buf.getvalue()
    buf.close()

    return f"Reporte_{today}.pdf", pdf_bytes

    ensure_reportlab()

    import io
    import os
    from datetime import date

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        KeepTogether,
    )

    today_prefix = date.today().isoformat()

    # --- detectar columnas (tolerante a schema)
    cols_orders = {r["name"] for r in conn.execute("PRAGMA table_info(orders);").fetchall()}
    cols_items = {r["name"] for r in conn.execute("PRAGMA table_info(order_items);").fetchall()}
    cols_mods = {r["name"] for r in conn.execute("PRAGMA table_info(order_item_mods);").fetchall()}

    has_payment = "payment_method" in cols_orders
    has_item_name_new = "product_name_snapshot" in cols_items
    has_item_name_old = "name_snapshot" in cols_items

    orders = conn.execute(
        """
        SELECT o.*, c.name AS courier_name
        FROM orders o
        LEFT JOIN couriers c ON c.id = o.courier_id
        WHERE o.created_at LIKE ?
        ORDER BY o.id ASC;
        """,
        (f"{today_prefix}%",),
    ).fetchall()

    order_ids = [int(o["id"]) for o in orders]

    items_by_order = {}
    mods_by_item = {}

    if order_ids:
        q = ",".join(["?"] * len(order_ids))
        items = conn.execute(
            f"""
            SELECT *
            FROM order_items
            WHERE order_id IN ({q})
            ORDER BY order_id ASC, id ASC;
            """,
            order_ids,
        ).fetchall()

        for it in items:
            items_by_order.setdefault(int(it["order_id"]), []).append(it)

        item_ids = [int(it["id"]) for it in items]
        if item_ids:
            q2 = ",".join(["?"] * len(item_ids))
            mods = conn.execute(
                f"""
                SELECT *
                FROM order_item_mods
                WHERE order_item_id IN ({q2})
                ORDER BY order_item_id ASC, id ASC;
                """,
                item_ids,
            ).fetchall()

            for m in mods:
                mods_by_item.setdefault(int(m["order_item_id"]), []).append(m)

    def is_transfer(pm: str) -> bool:
        pm = (pm or "").strip().lower()
        return pm.startswith("transf") or "transfer" in pm or "banco" in pm or "mp" in pm or "mercado" in pm

    def item_name(it) -> str:
        if has_item_name_new and it["product_name_snapshot"]:
            return str(it["product_name_snapshot"])
        if has_item_name_old and it["name_snapshot"]:
            return str(it["name_snapshot"])
        return "Producto"

    # --- resumen efectivo por repartidor
    courier_seen = set()
    courier_cash_sales = {}

    for o in orders:
        cname = (o["courier_name"] or "Sin repartidor").strip()
        courier_seen.add(cname)

        pm = (o["payment_method"] if has_payment else "cash") if has_payment else "cash"
        pm = pm or "cash"
        if not is_transfer(pm):
            courier_cash_sales[cname] = courier_cash_sales.get(cname, 0) + int(o["total_cents"] or 0)

    # ---------- Estética (creativa, smooth) ----------
    INK = colors.HexColor("#0B1220")        # texto principal
    MUTED = colors.HexColor("#6B7280")      # texto secundario
    BG = colors.HexColor("#F6F7FB")         # fondo suave
    CARD = colors.white                     # card
    BORDER = colors.HexColor("#E7EAF2")     # borde sutil
    SHADOW = colors.HexColor("#D9DDE8")     # sombra soft
    ACCENT = colors.HexColor("#7C3AED")     # violeta creativo (podés cambiarlo si querés)
    ACCENT2 = colors.HexColor("#22C55E")    # verde suave para highlights
    CHIP_BG = colors.HexColor("#EEEAFB")    # chip suave

    styles = getSampleStyleSheet()
    s_brand = ParagraphStyle(
        "s_brand",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=INK,
    )
    s_title = ParagraphStyle(
        "s_title",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=INK,
    )
    s_sub = ParagraphStyle(
        "s_sub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        textColor=MUTED,
    )
    s_h = ParagraphStyle(
        "s_h",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=INK,
        spaceBefore=10,
        spaceAfter=6,
    )
    s_small = ParagraphStyle(
        "s_small",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.6,
        leading=13,
        textColor=INK,
    )
    s_muted = ParagraphStyle(
        "s_muted",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.2,
        leading=12.5,
        textColor=MUTED,
    )
    s_kpi = ParagraphStyle(
        "s_kpi",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
        textColor=ACCENT,
    )

    # Logo (si existe)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    logo_path = os.path.join(base_dir, "logo.png")
    logo_reader = None
    if os.path.exists(logo_path):
        try:
            logo_reader = ImageReader(logo_path)
        except Exception:
            logo_reader = None

    def draw_bg_header_footer(canvas, doc):
        w, h = A4
        canvas.saveState()

        # Fondo general
        canvas.setFillColor(BG)
        canvas.rect(0, 0, w, h, fill=1, stroke=0)

        # Blobs decorativos (creativos, suaves)
        canvas.setFillColor(colors.HexColor("#EFEAFE"))
        canvas.circle(w * 0.12, h * 0.92, 65, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#E9F8EF"))
        canvas.circle(w * 0.95, h * 0.84, 85, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#FFF1E8"))
        canvas.circle(w * 0.85, h * 0.15, 95, fill=1, stroke=0)

        # Header card (rounded) con “shadow”
        left = 14 * mm
        right = w - 14 * mm
        header_h = 24 * mm
        top = h - 14 * mm
        y0 = top - header_h

        canvas.setFillColor(SHADOW)
        canvas.roundRect(left + 1.5, y0 - 1.5, right - left, header_h, 10, fill=1, stroke=0)

        canvas.setFillColor(CARD)
        canvas.roundRect(left, y0, right - left, header_h, 10, fill=1, stroke=0)

        # Accent bar
        canvas.setFillColor(ACCENT)
        canvas.roundRect(left, y0 + header_h - 6, right - left, 6, 10, fill=1, stroke=0)

        # Logo (si está)
        if logo_reader is not None:
            # Ajuste nice: logo en un “chip” a la izquierda
            chip_w = 18 * mm
            chip_h = 18 * mm
            chip_x = left + 8
            chip_y = y0 + (header_h - chip_h) / 2

            canvas.setFillColor(colors.HexColor("#F4F2FF"))
            canvas.roundRect(chip_x, chip_y, chip_w, chip_h, 6, fill=1, stroke=0)

            # Logo centrado en chip
            try:
                canvas.drawImage(
                    logo_reader,
                    chip_x + 2,
                    chip_y + 2,
                    chip_w - 4,
                    chip_h - 4,
                    preserveAspectRatio=True,
                    mask="auto",
                )
            except Exception:
                pass

        # Footer minimal
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(14 * mm, 10 * mm, f"Reporte diario • {today_prefix}")
        canvas.drawRightString(w - 14 * mm, 10 * mm, f"Página {canvas.getPageNumber()}")

        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=42 * mm,    # deja lugar al header card
        bottomMargin=16 * mm,
        title=f"Reporte_{today_prefix}",
    )

    story = []

    # Title block (más creativo)
    story.append(Paragraph("Reporte del día", s_title))
    story.append(Paragraph(f"<b>{today_prefix}</b> • pedidos + resumen de efectivo", s_sub))
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph("Pedidos", s_h))

    if not orders:
        empty = Table([[Paragraph("Hoy no hubo pedidos. 🎉", s_small)]], colWidths=[None])
        empty.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), CARD),
                    ("BOX", (0, 0), (-1, -1), 0.7, BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 12),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ]
            )
        )
        story.append(empty)
    else:
        for o in orders:
            oid = int(o["id"])
            dt = fmt_dt(o["created_at"])
            cname = (o["courier_name"] or "Sin repartidor").strip()
            pm = (o["payment_method"] if has_payment else "cash") if has_payment else "cash"
            pm = pm or "cash"
            total = money(int(o["total_cents"] or 0))

            phone = o["phone"] if "phone" in o.keys() else ""
            address = o["address"] if "address" in o.keys() else ""

            # Chips (Pago / Total)
            chip_pay = Table([[Paragraph(f"Pago: <b>{pm}</b>", s_muted)]], colWidths=[44 * mm])
            chip_pay.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), CHIP_BG),
                        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#DAD3FF")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )

            chip_total = Table([[Paragraph(f"<b>${total}</b>", s_kpi)]], colWidths=[28 * mm])
            chip_total.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ECFDF5")),
                        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#BBF7D0")),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )

            chip_row = Table([[chip_pay, chip_total]], colWidths=[None, 32 * mm])
            chip_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

            header = Paragraph(
                f"<b>#{oid}</b> &nbsp;&nbsp; {dt['date']} {dt['time']} &nbsp;&nbsp; <font color='#7C3AED'><b>{cname}</b></font>",
                s_small,
            )
            contact = Paragraph(
                f"<font color='#6B7280'>Tel:</font> {phone} &nbsp;&nbsp; <font color='#6B7280'>Dir:</font> {address}",
                s_muted,
            )

            # Items list (minimal y lindo)
            item_rows = []
            for it in items_by_order.get(oid, []):
                qty = int(it["qty"] or 1)
                nm = item_name(it)

                iid = int(it["id"])
                mods_txt = []
                for md in mods_by_item.get(iid, []):
                    kind = (md["kind"] if "kind" in cols_mods else "") or ""
                    kind = kind.strip().lower()
                    nm2 = (md["name_snapshot"] if "name_snapshot" in cols_mods else "") or ""
                    pcents = int(md["price_cents"] or 0) if "price_cents" in cols_mods else 0

                    if kind == "remove":
                        mods_txt.append(f"sin {nm2}")
                    else:
                        mods_txt.append(f"+{nm2}" + (f" (+${money(pcents)})" if pcents else ""))

                mods_html = "<br/>".join(mods_txt) if mods_txt else "<font color='#9CA3AF'>—</font>"
                item_rows.append([
                    Paragraph(f"<b>{qty}×</b>", s_small),
                    Paragraph(nm, s_small),
                    Paragraph(mods_html, s_muted),
                ])

            if not item_rows:
                item_rows = [[Paragraph("—", s_muted), Paragraph("Sin ítems", s_muted), Paragraph("—", s_muted)]]

            items_tbl = Table(item_rows, colWidths=[12 * mm, 78 * mm, None], hAlign="LEFT")
            items_tbl.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 2),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#EEF0F6")),
                    ]
                )
            )

            # Card con “shadow” suave (2 tablas: shadow + card)
            card_inner = Table(
                [[header],
                 [chip_row],
                 [contact],
                 [Spacer(1, 2 * mm)],
                 [items_tbl]],
                colWidths=[None],
            )
            card_inner.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), CARD),
                        ("BOX", (0, 0), (-1, -1), 0.7, BORDER),
                        ("LEFTPADDING", (0, 0), (-1, -1), 12),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                        ("TOPPADDING", (0, 0), (-1, -1), 12),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                    ]
                )
            )

            shadow_wrap = Table([[card_inner]], colWidths=[None])
            shadow_wrap.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#00000000")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ]
                )
            )

            story.append(KeepTogether([shadow_wrap, Spacer(1, 7 * mm)]))

    # Resumen
    story.append(Paragraph("Resumen de efectivo (con cambio)", s_h))
    story.append(Paragraph("Incluye el cambio configurado de cada repartidor si tuvo pedidos.", s_muted))
    story.append(Spacer(1, 4 * mm))

    if not courier_seen:
        summary = Table([[Paragraph("No hubo repartidores con pedidos.", s_small)]], colWidths=[None])
        summary.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), CARD),
                    ("BOX", (0, 0), (-1, -1), 0.7, BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 12),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ]
            )
        )
        story.append(summary)
    else:
        sum_rows = [[Paragraph("<b>Repartidor</b>", s_small), Paragraph("<b>Efectivo</b>", s_small)]]
        for cname in sorted(courier_seen):
            handled = courier_cash_sales.get(cname, 0) + _courier_float_cents(conn, cname)
            sum_rows.append([Paragraph(cname, s_small), Paragraph(f"<b>${money(handled)}</b>", s_small)])

        summary_tbl = Table(sum_rows, colWidths=[None, 42 * mm], hAlign="LEFT")
        summary_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5FF")),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.8, BORDER),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FBFBFE")]),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )

        summary_card = Table([[summary_tbl]], colWidths=[None])
        summary_card.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), CARD),
                    ("BOX", (0, 0), (-1, -1), 0.7, BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(summary_card)

    doc.build(story, onFirstPage=draw_bg_header_footer, onLaterPages=draw_bg_header_footer)

    pdf_bytes = buf.getvalue()
    buf.close()

    filename = f"Reporte_{today_prefix}.pdf"
    return filename, pdf_bytes

    ensure_reportlab()

    import io
    from datetime import date

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        KeepTogether,
    )

    today_prefix = date.today().isoformat()

    # --- detectar columnas (tolerante a schema)
    cols_orders = {r["name"] for r in conn.execute("PRAGMA table_info(orders);").fetchall()}
    cols_items = {r["name"] for r in conn.execute("PRAGMA table_info(order_items);").fetchall()}
    cols_mods = {r["name"] for r in conn.execute("PRAGMA table_info(order_item_mods);").fetchall()}

    has_payment = "payment_method" in cols_orders
    has_item_name_new = "product_name_snapshot" in cols_items
    has_item_name_old = "name_snapshot" in cols_items

    orders = conn.execute(
        """
        SELECT o.*, c.name AS courier_name
        FROM orders o
        LEFT JOIN couriers c ON c.id = o.courier_id
        WHERE o.created_at LIKE ?
        ORDER BY o.id ASC;
        """,
        (f"{today_prefix}%",),
    ).fetchall()

    order_ids = [int(o["id"]) for o in orders]

    items_by_order = {}
    mods_by_item = {}

    if order_ids:
        q = ",".join(["?"] * len(order_ids))
        items = conn.execute(
            f"""
            SELECT *
            FROM order_items
            WHERE order_id IN ({q})
            ORDER BY order_id ASC, id ASC;
            """,
            order_ids,
        ).fetchall()

        for it in items:
            items_by_order.setdefault(int(it["order_id"]), []).append(it)

        item_ids = [int(it["id"]) for it in items]
        if item_ids:
            q2 = ",".join(["?"] * len(item_ids))
            mods = conn.execute(
                f"""
                SELECT *
                FROM order_item_mods
                WHERE order_item_id IN ({q2})
                ORDER BY order_item_id ASC, id ASC;
                """,
                item_ids,
            ).fetchall()

            for m in mods:
                mods_by_item.setdefault(int(m["order_item_id"]), []).append(m)

    def is_transfer(pm: str) -> bool:
        pm = (pm or "").strip().lower()
        return pm.startswith("transf") or "transfer" in pm or "banco" in pm or "mp" in pm or "mercado" in pm

    def item_name(it) -> str:
        if has_item_name_new and it["product_name_snapshot"]:
            return str(it["product_name_snapshot"])
        if has_item_name_old and it["name_snapshot"]:
            return str(it["name_snapshot"])
        return "Producto"

    # --- resumen efectivo por repartidor
    courier_seen = set()
    courier_cash_sales = {}

    for o in orders:
        cname = (o["courier_name"] or "Sin repartidor").strip()
        courier_seen.add(cname)

        pm = (o["payment_method"] if has_payment else "cash") if has_payment else "cash"
        pm = pm or "cash"
        if not is_transfer(pm):
            courier_cash_sales[cname] = courier_cash_sales.get(cname, 0) + int(o["total_cents"] or 0)

    # ---------- Diseño (paleta suave) ----------
    C_INK = colors.HexColor("#111827")      # texto principal (gris muy oscuro)
    C_MUTED = colors.HexColor("#6B7280")    # texto secundario
    C_SOFT_BG = colors.HexColor("#F7F7FB")  # fondo suave
    C_CARD = colors.HexColor("#FFFFFF")     # blanco tarjeta
    C_BORDER = colors.HexColor("#E5E7EB")   # borde sutil
    C_ACCENT = colors.HexColor("#5B6CFF")   # acento (azul suave)
    C_ACCENT_SOFT = colors.HexColor("#EEF0FF")  # acento clarito

    styles = getSampleStyleSheet()
    s_title = ParagraphStyle(
        "s_title",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.white,
    )
    s_sub = ParagraphStyle(
        "s_sub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        textColor=colors.white,
    )
    s_h = ParagraphStyle(
        "s_h",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=C_INK,
        spaceBefore=10,
        spaceAfter=6,
    )
    s_small = ParagraphStyle(
        "s_small",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13,
        textColor=C_INK,
    )
    s_muted = ParagraphStyle(
        "s_muted",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=C_MUTED,
    )
    s_badge = ParagraphStyle(
        "s_badge",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=C_ACCENT,
    )

    # ---------- Header / Footer ----------
    def draw_header_footer(canvas, doc):
        w, h = A4

        # Fondo suave general (arriba no hace falta, pero queda prolijo)
        canvas.saveState()

        # Header banner
        banner_h = 22 * mm
        canvas.setFillColor(C_ACCENT)
        canvas.rect(0, h - banner_h, w, banner_h, fill=1, stroke=0)

        # “píldora” suave a la derecha para la fecha (detalle lindo)
        pill_w = 52 * mm
        pill_h = 9 * mm
        canvas.setFillColor(colors.HexColor("#4657FF"))
        canvas.roundRect(w - 14 * mm - pill_w, h - 15 * mm, pill_w, pill_h, 4 * mm, fill=1, stroke=0)

        # Footer
        canvas.setFillColor(C_MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(14 * mm, 10 * mm, f"Reporte diario • {today_prefix}")
        canvas.drawRightString(w - 14 * mm, 10 * mm, f"Página {canvas.getPageNumber()}")

        canvas.restoreState()

    # ---------- PDF ----------
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=28 * mm,     # deja espacio para banner
        bottomMargin=16 * mm,
        title=f"Reporte_{today_prefix}",
    )

    story = []

    # Cabecera “diseñada”
    # (La dibuja el canvas, acá agregamos texto alineado con el margen)
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("MagikBurger", s_title))
    story.append(Paragraph(f"Reporte diario • <b>{today_prefix}</b>", s_sub))
    story.append(Spacer(1, 10 * mm))

    # Sección pedidos
    story.append(Paragraph("Pedidos del día", s_h))

    if not orders:
        empty_card = Table(
            [[Paragraph("No hubo pedidos registrados hoy.", s_small)]],
            colWidths=[None],
        )
        empty_card.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), C_CARD),
                    ("BOX", (0, 0), (-1, -1), 0.6, C_BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        story.append(empty_card)
    else:
        for o in orders:
            oid = int(o["id"])
            dt = fmt_dt(o["created_at"])
            cname = (o["courier_name"] or "Sin repartidor").strip()
            pm = (o["payment_method"] if has_payment else "cash") if has_payment else "cash"
            pm = pm or "cash"
            total = money(int(o["total_cents"] or 0))

            phone = o["phone"] if "phone" in o.keys() else ""
            address = o["address"] if "address" in o.keys() else ""

            # Badges (Pago + Total)
            badge_row = Table(
                [[
                    Paragraph(f"Pago: <b>{pm}</b>", s_muted),
                    Paragraph(f"<b>${total}</b>", s_badge),
                ]],
                colWidths=[None, 28 * mm],
            )
            badge_row.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (1, 0), (1, 0), C_ACCENT_SOFT),
                        ("BOX", (1, 0), (1, 0), 0.6, colors.HexColor("#D9DDFF")),
                        ("ALIGN", (1, 0), (1, 0), "CENTER"),
                        ("LEFTPADDING", (1, 0), (1, 0), 6),
                        ("RIGHTPADDING", (1, 0), (1, 0), 6),
                        ("TOPPADDING", (1, 0), (1, 0), 3),
                        ("BOTTOMPADDING", (1, 0), (1, 0), 3),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (0, 0), 0),
                        ("RIGHTPADDING", (0, 0), (0, 0), 0),
                        ("TOPPADDING", (0, 0), (0, 0), 0),
                        ("BOTTOMPADDING", (0, 0), (0, 0), 0),
                    ]
                )
            )

            header = Paragraph(f"<b>#{oid}</b> &nbsp;&nbsp; {dt['date']} {dt['time']} &nbsp;&nbsp; <b>{cname}</b>", s_small)
            contact = Paragraph(f"<font color='#6B7280'>Tel:</font> {phone} &nbsp;&nbsp; <font color='#6B7280'>Dir:</font> {address}", s_muted)

            # Items: tabla minimalista (sin grid fuerte)
            item_rows = []
            for it in items_by_order.get(oid, []):
                qty = int(it["qty"] or 1)
                nm = item_name(it)

                iid = int(it["id"])
                mods_txt = []
                for md in mods_by_item.get(iid, []):
                    kind = (md["kind"] if "kind" in cols_mods else "") or ""
                    kind = kind.strip().lower()
                    nm2 = (md["name_snapshot"] if "name_snapshot" in cols_mods else "") or ""
                    pcents = int(md["price_cents"] or 0) if "price_cents" in cols_mods else 0

                    if kind == "remove":
                        mods_txt.append(f"sin {nm2}")
                    else:
                        mods_txt.append(f"+{nm2}" + (f" (+${money(pcents)})" if pcents else ""))

                mods_html = "<br/>".join(mods_txt) if mods_txt else "<font color='#9CA3AF'>—</font>"
                item_rows.append([
                    Paragraph(f"<b>{qty}×</b>", s_small),
                    Paragraph(nm, s_small),
                    Paragraph(mods_html, s_muted),
                ])

            if not item_rows:
                item_rows = [[Paragraph("—", s_muted), Paragraph("Sin ítems", s_muted), Paragraph("—", s_muted)]]

            items_tbl = Table(
                item_rows,
                colWidths=[12 * mm, 78 * mm, None],
                hAlign="LEFT",
            )
            items_tbl.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 2),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#F0F1F5")),
                    ]
                )
            )

            # Card contenedor (fondo blanco + borde suave + padding)
            card = Table(
                [[
                    header,
                ],
                 [
                    badge_row,
                 ],
                 [
                    contact,
                 ],
                 [
                    Spacer(1, 2 * mm),
                 ],
                 [
                    items_tbl,
                 ]],
                colWidths=[None],
            )
            card.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), C_CARD),
                        ("BOX", (0, 0), (-1, -1), 0.7, C_BORDER),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 10),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ]
                )
            )

            story.append(KeepTogether([card, Spacer(1, 8 * mm)]))

    # Sección resumen
    story.append(Paragraph("Resumen (efectivo manejado)", s_h))
    story.append(Paragraph("Incluye el cambio configurado de cada repartidor si tuvo pedidos.", s_muted))
    story.append(Spacer(1, 4 * mm))

    if not courier_seen:
        summary_card = Table([[Paragraph("No hubo repartidores con pedidos.", s_small)]], colWidths=[None])
        summary_card.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), C_CARD),
                    ("BOX", (0, 0), (-1, -1), 0.7, C_BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        story.append(summary_card)
    else:
        sum_rows = [[Paragraph("<b>Repartidor</b>", s_small), Paragraph("<b>Efectivo</b>", s_small)]]
        for cname in sorted(courier_seen):
            handled = courier_cash_sales.get(cname, 0) + _courier_float_cents(conn, cname)
            sum_rows.append([Paragraph(cname, s_small), Paragraph(f"${money(handled)}", s_small)])

        summary_tbl = Table(sum_rows, colWidths=[None, 40 * mm], hAlign="LEFT")
        summary_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), C_SOFT_BG),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.8, C_BORDER),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FBFBFE")]),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )

        summary_card = Table([[summary_tbl]], colWidths=[None])
        summary_card.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), C_CARD),
                    ("BOX", (0, 0), (-1, -1), 0.7, C_BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(summary_card)

    doc.build(story, onFirstPage=draw_header_footer, onLaterPages=draw_header_footer)

    pdf_bytes = buf.getvalue()
    buf.close()

    filename = f"Reporte_{today_prefix}.pdf"
    return filename, pdf_bytes

    ensure_reportlab()

    import io
    from datetime import date

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        KeepTogether,
    )

    today_prefix = date.today().isoformat()

    # --- detectar columnas (tolerante a schema)
    cols_orders = {r["name"] for r in conn.execute("PRAGMA table_info(orders);").fetchall()}
    cols_items = {r["name"] for r in conn.execute("PRAGMA table_info(order_items);").fetchall()}
    cols_mods = {r["name"] for r in conn.execute("PRAGMA table_info(order_item_mods);").fetchall()}

    has_payment = "payment_method" in cols_orders
    has_item_name_new = "product_name_snapshot" in cols_items
    has_item_name_old = "name_snapshot" in cols_items

    orders = conn.execute(
        """
        SELECT o.*, c.name AS courier_name
        FROM orders o
        LEFT JOIN couriers c ON c.id = o.courier_id
        WHERE o.created_at LIKE ?
        ORDER BY o.id ASC;
        """,
        (f"{today_prefix}%",),
    ).fetchall()

    order_ids = [int(o["id"]) for o in orders]

    items_by_order = {}
    mods_by_item = {}

    if order_ids:
        q = ",".join(["?"] * len(order_ids))
        items = conn.execute(
            f"""
            SELECT *
            FROM order_items
            WHERE order_id IN ({q})
            ORDER BY order_id ASC, id ASC;
            """,
            order_ids,
        ).fetchall()

        for it in items:
            items_by_order.setdefault(int(it["order_id"]), []).append(it)

        item_ids = [int(it["id"]) for it in items]
        if item_ids:
            q2 = ",".join(["?"] * len(item_ids))
            mods = conn.execute(
                f"""
                SELECT *
                FROM order_item_mods
                WHERE order_item_id IN ({q2})
                ORDER BY order_item_id ASC, id ASC;
                """,
                item_ids,
            ).fetchall()

            for m in mods:
                mods_by_item.setdefault(int(m["order_item_id"]), []).append(m)

    def is_transfer(pm: str) -> bool:
        pm = (pm or "").strip().lower()
        return pm.startswith("transf") or "transfer" in pm or "banco" in pm or "mp" in pm or "mercado" in pm

    def item_name(it) -> str:
        if has_item_name_new and it["product_name_snapshot"]:
            return str(it["product_name_snapshot"])
        if has_item_name_old and it["name_snapshot"]:
            return str(it["name_snapshot"])
        return "Producto"

    # --- resumen efectivo por repartidor
    courier_seen = set()
    courier_cash_sales = {}

    for o in orders:
        cname = (o["courier_name"] or "Sin repartidor").strip()
        courier_seen.add(cname)

        pm = (o["payment_method"] if has_payment else "cash") if has_payment else "cash"
        pm = pm or "cash"
        if not is_transfer(pm):
            courier_cash_sales[cname] = courier_cash_sales.get(cname, 0) + int(o["total_cents"] or 0)

    # ---------- PDF (Platypus) ----------
    buf = io.BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Reporte_{today_prefix}",
    )

    styles = getSampleStyleSheet()
    # Paleta sobria (sin tocar tu UI)
    title_style = ParagraphStyle(
        "title_style",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#111827"),  # gris muy oscuro
        spaceAfter=6,
    )
    sub_style = ParagraphStyle(
        "sub_style",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#374151"),
        spaceAfter=8,
    )
    h2_style = ParagraphStyle(
        "h2_style",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#111827"),
        spaceBefore=10,
        spaceAfter=6,
    )
    small_style = ParagraphStyle(
        "small_style",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#111827"),
    )
    muted_style = ParagraphStyle(
        "muted_style",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#6B7280"),
    )

    story = []
    story.append(Paragraph("MagikBurger - Reporte diario", title_style))
    story.append(Paragraph(f"Fecha: <b>{today_prefix}</b>", sub_style))
    story.append(Spacer(1, 6))

    # --------- Sección pedidos ----------
    story.append(Paragraph("Pedidos del día", h2_style))

    if not orders:
        story.append(Paragraph("No hubo pedidos registrados hoy.", sub_style))
    else:
        for o in orders:
            oid = int(o["id"])
            dt = fmt_dt(o["created_at"])
            cname = (o["courier_name"] or "Sin repartidor").strip()
            pm = (o["payment_method"] if has_payment else "cash") if has_payment else "cash"
            pm = pm or "cash"
            total = money(int(o["total_cents"] or 0))

            phone = o["phone"] if "phone" in o.keys() else ""
            address = o["address"] if "address" in o.keys() else ""

            header = f"<b>#{oid}</b> &nbsp;&nbsp; {dt['date']} {dt['time']} &nbsp;&nbsp; <b>{cname}</b>"
            meta = f"Pago: <b>{pm}</b> &nbsp;&nbsp; Total: <b>${total}</b>"
            contact = f"<font color='#6B7280'>Tel:</font> {phone} &nbsp;&nbsp; <font color='#6B7280'>Dir:</font> {address}"

            block = []
            block.append(Paragraph(header, small_style))
            block.append(Paragraph(meta, small_style))
            block.append(Paragraph(contact, muted_style))
            block.append(Spacer(1, 4))

            # Tabla de items
            rows = [["Cant.", "Producto", "Mods"]]
            for it in items_by_order.get(oid, []):
                qty = int(it["qty"] or 1)
                nm = item_name(it)

                iid = int(it["id"])
                mods_txt = []
                for md in mods_by_item.get(iid, []):
                    kind = (md["kind"] if "kind" in cols_mods else "") or ""
                    kind = kind.strip().lower()
                    nm2 = (md["name_snapshot"] if "name_snapshot" in cols_mods else "") or ""
                    pcents = int(md["price_cents"] or 0) if "price_cents" in cols_mods else 0

                    if kind == "remove":
                        mods_txt.append(f"sin {nm2}")
                    else:
                        if pcents:
                            mods_txt.append(f"+{nm2} (+${money(pcents)})")
                        else:
                            mods_txt.append(f"+{nm2}")

                rows.append([str(qty), nm, "<br/>".join(mods_txt) if mods_txt else "—"])

            t = Table(
                rows,
                colWidths=[16 * mm, 80 * mm, None],
                hAlign="LEFT",
            )
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111827")),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, 0), 9),
                        ("ALIGN", (0, 0), (0, -1), "CENTER"),
                        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                        ("FONTSIZE", (0, 1), (-1, -1), 9),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )

            block.append(t)
            block.append(Spacer(1, 10))

            # Mantener cada pedido junto lo más posible
            story.append(KeepTogether(block))

    # --------- Sección resumen ----------
    story.append(Paragraph("Resumen - Efectivo manejado por repartidor", h2_style))
    story.append(Paragraph("(incluye $1500 de cambio una vez por repartidor si tuvo pedidos)", sub_style))

    if not courier_seen:
        story.append(Paragraph("No hubo repartidores con pedidos.", sub_style))
    else:
        sum_rows = [["Repartidor", "Efectivo manejado"]]
        for cname in sorted(courier_seen):
            handled = courier_cash_sales.get(cname, 0) + _courier_float_cents(conn, cname)
            sum_rows.append([cname, f"${money(handled)}"])

        st = Table(sum_rows, colWidths=[None, 45 * mm], hAlign="LEFT")
        st.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 1), (-1, -1), 10),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(st)

    doc.build(story)

    pdf_bytes = buf.getvalue()
    buf.close()

    filename = f"Reporte_{today_prefix}.pdf"
    return filename, pdf_bytes



    def _item_name(row: sqlite3.Row) -> str:
        # Compat: prioriza snapshot nuevo
        if "product_name_snapshot" in row.keys() and row["product_name_snapshot"]:
            return str(row["product_name_snapshot"])
        if "name_snapshot" in row.keys() and row["name_snapshot"]:
            return str(row["name_snapshot"])
        return "Producto"

    def _is_transfer(pm: str) -> bool:
        pm = (pm or "").strip().lower()
        return pm.startswith("transf") or "transfer" in pm or "banco" in pm or "mp" in pm or "mercado" in pm

    # Calcular efectivo manejado por repartidor (efectivo ventas + $1500 si apareció)
    courier_cash_sales: dict[str, int] = {}  # nombre -> cents (solo efectivo, sin transfer)
    courier_seen: set[str] = set()

    for o in orders:
        cname = (o["courier_name"] or "Sin repartidor").strip()
        courier_seen.add(cname)

        pm = (o["payment_method"] or "cash")
        if not _is_transfer(pm):
            courier_cash_sales[cname] = courier_cash_sales.get(cname, 0) + int(o["total_cents"] or 0)

    courier_cash_handled = {}
    for cname in sorted(courier_seen):
        cash_sales = courier_cash_sales.get(cname, 0)
        # Si apareció (tiene al menos 1 pedido), sumamos el float una vez
        courier_cash_handled[cname] = cash_sales + _courier_float_cents(conn, cname)

    # Crear carpeta y path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(base_dir, "daily_reports")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"Reporte_{date_prefix}.pdf")

    # PDF
    c = canvas.Canvas(out_path, pagesize=A4)
    w, h = A4

    x_left = 15 * mm
    y = h - 18 * mm

    # Header
    c.setFont("Helvetica-Bold", 16)
    c.drawString(x_left, y, "MagikBurger - Reporte diario")
    y -= 7 * mm
    c.setFont("Helvetica", 11)
    c.drawString(x_left, y, f"Fecha: {date_prefix}")
    y -= 10 * mm

    # Pedidos
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x_left, y, "Pedidos del día")
    y -= 6 * mm
    c.setLineWidth(0.5)
    c.line(x_left, y, w - x_left, y)
    y -= 6 * mm

    if not orders:
        c.setFont("Helvetica", 11)
        c.drawString(x_left, y, "No hubo pedidos registrados en el día.")
        y -= 10 * mm
    else:
        for o in orders:
            # salto de página si hace falta
            if y < 25 * mm:
                c.showPage()
                y = h - 18 * mm
                c.setFont("Helvetica-Bold", 12)
                c.drawString(x_left, y, "Pedidos del día (continuación)")
                y -= 6 * mm
                c.line(x_left, y, w - x_left, y)
                y -= 8 * mm

            oid = int(o["id"])
            dt = fmt_dt(o["created_at"])
            cname = (o["courier_name"] or "Sin repartidor").strip()
            pm = (o["payment_method"] or "cash").strip()
            total = money(int(o["total_cents"] or 0))
            phone = o["phone"]
            address = o["address"]

            c.setFont("Helvetica-Bold", 11)
            c.drawString(x_left, y, f"#{oid}  {dt['date']} {dt['time']}  -  {cname}  -  ${total}  -  {pm}")
            y -= 5 * mm

            c.setFont("Helvetica", 10)
            c.drawString(x_left, y, f"Tel: {phone}  |  Dir: {address}")
            y -= 5 * mm

            # Items
            for it in items_by_order.get(oid, []):
                if y < 25 * mm:
                    c.showPage()
                    y = h - 18 * mm

                qty = int(it["qty"] or 1)
                name = _item_name(it)

                c.setFont("Helvetica", 10)
                c.drawString(x_left + 5 * mm, y, f"- {qty} x {name}")
                y -= 4.5 * mm

                # Mods por item
                iid = int(it["id"])
                for md in mods_by_item.get(iid, []):
                    kind = (md["kind"] or "").strip().lower()
                    iname = (md["name_snapshot"] or "").strip()
                    pcents = int(md["price_cents"] or 0)
                    if kind == "remove":
                        txt = f"   • sin {iname}"
                    else:
                        extra = f" (+${money(pcents)})" if pcents else ""
                        txt = f"   • +{iname}{extra}"
                    c.setFont("Helvetica", 9)
                    c.drawString(x_left + 10 * mm, y, txt)
                    y -= 4 * mm

            y -= 3 * mm
            c.setLineWidth(0.3)
            c.line(x_left, y, w - x_left, y)
            y -= 6 * mm

    # Resumen efectivo por repartidor
    if y < 60 * mm:
        c.showPage()
        y = h - 18 * mm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(x_left, y, "Resumen - Efectivo manejado por repartidor")
    y -= 6 * mm
    c.setLineWidth(0.5)
    c.line(x_left, y, w - x_left, y)
    y -= 8 * mm

    c.setFont("Helvetica", 11)
    c.drawString(x_left, y, f"(Incluye $1500 de cambio una vez por repartidor si tuvo pedidos)")
    y -= 8 * mm

    if not courier_cash_handled:
        c.setFont("Helvetica", 11)
        c.drawString(x_left, y, "No hubo repartidores con pedidos.")
        y -= 8 * mm
    else:
        for cname, cents in courier_cash_handled.items():
            if y < 25 * mm:
                c.showPage()
                y = h - 18 * mm
            c.setFont("Helvetica", 11)
            c.drawString(x_left, y, f"- {cname}: ${money(cents)}")
            y -= 6 * mm

    c.save()
    return out_path


def require_admin():
    if not session.get(SESSION_ADMIN_KEY):
        return redirect(url_for("admin_login"))
    return None


# -----------------------------
# Home
# -----------------------------
@app.get("/")
def index():
    return render_template("index.html")


# Compat: algunos templates usan url_for('home').
# No cambiamos diseño ni flujos: simplemente damos el endpoint esperado.
@app.get("/home")
def home():
    return redirect(url_for("index"))


# -----------------------------
# Bootstrap
# -----------------------------
@app.get("/api/bootstrap")
def api_bootstrap():
    conn = db()
    try:
        products = conn.execute(
            "SELECT id, name, price_cents, active FROM products ORDER BY active DESC, name COLLATE NOCASE ASC;"
        ).fetchall()

        ingredients = conn.execute(
            "SELECT id, name, extra_price_cents, active FROM ingredients ORDER BY active DESC, name COLLATE NOCASE ASC;"
        ).fetchall()

        couriers = conn.execute(
            "SELECT id, name FROM couriers ORDER BY name COLLATE NOCASE ASC;"
        ).fetchall()

        # base ingredients map (product_id -> [ingredient_id])
        base = conn.execute(
            "SELECT product_id, ingredient_id FROM product_base_ingredients"
        ).fetchall()

        base_map: Dict[int, List[int]] = {}
        for r in base:
            base_map.setdefault(int(r["product_id"]), []).append(int(r["ingredient_id"]))

        out_products = []
        for p in products:
            out_products.append(
                {
                    "id": int(p["id"]),
                    "name": p["name"],
                    "price_cents": int(p["price_cents"] or 0),
                    "active": int(p["active"] or 0),
                    "base_ingredient_ids": base_map.get(int(p["id"]), []),
                }
            )

        # Promos activas con sus componentes (productos que la integran)
        out_promos = []
        if _table_has_column(conn, "promos", "id"):
            promos = conn.execute(
                "SELECT id, name, price_cents, active FROM promos WHERE active=1 ORDER BY name COLLATE NOCASE ASC;"
            ).fetchall()
            comp_rows = conn.execute(
                "SELECT promo_id, product_id FROM promo_components ORDER BY sort_order ASC, id ASC;"
            ).fetchall()
            comp_map: Dict[int, List[int]] = {}
            for r in comp_rows:
                comp_map.setdefault(int(r["promo_id"]), []).append(int(r["product_id"]))
            for pr in promos:
                out_promos.append(
                    {
                        "id": int(pr["id"]),
                        "name": pr["name"],
                        "price_cents": int(pr["price_cents"] or 0),
                        "component_product_ids": comp_map.get(int(pr["id"]), []),
                    }
                )

        # Configuración (copias de ticket, envío por defecto)
        settings_out = {
            "ticket_copies": int(get_setting(conn, "ticket_copies", "1") or "1"),
            "delivery_fee_cents": int(get_setting(conn, "delivery_fee_cents", "0") or "0"),
        }

        return jsonify(
            {
                "products": out_products,
                "ingredients": [
                    {
                        "id": int(i["id"]),
                        "name": i["name"],
                        "extra_price_cents": int(i["extra_price_cents"] or 0),
                        "active": int(i["active"] or 0),
                    }
                    for i in ingredients
                ],
                "couriers": [
                    {"id": int(c["id"]), "name": c["name"]} for c in couriers
                ],
                "promos": out_promos,
                "settings": settings_out,
            }
        )
    finally:
        conn.close()


@app.post("/api/settings")
def api_set_settings():
    """Guarda configuración persistente (lista blanca de claves)."""
    data = request.get_json(silent=True) or {}
    allowed = {"ticket_copies", "delivery_fee_cents"}
    conn = db()
    try:
        for key, val in data.items():
            if key not in allowed:
                continue
            try:
                ival = int(val)
            except Exception:
                continue
            if key == "ticket_copies":
                ival = max(1, min(ival, 10))
            elif key == "delivery_fee_cents":
                ival = max(0, ival)
            set_setting(conn, key, ival)
        conn.commit()
        return jsonify({"ok": True})
    except sqlite3.Error as e:
        conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


# -----------------------------
# Board
# -----------------------------
@app.get("/api/board")
def api_board():
    conn = db()
    try:
        rows = conn.execute(
            "SELECT o.*, c.name AS courier_name "
            "FROM orders o LEFT JOIN couriers c ON c.id=o.courier_id "
            "ORDER BY o.id DESC;"
        ).fetchall()

        out = {"new": [], "kitchen": [], "way": [], "done": []}
        for r in rows:
            st = (r["status"] or "new").strip()
            if st not in out:
                st = "new"
            out[st].append(
                {
                    "id": int(r["id"]),
                    "created_at": r["created_at"],
                    "phone": r["phone"],
                    "address": r["address"],
                    "courier_id": r["courier_id"],
                    "courier_name": r["courier_name"],
                    "total_cents": int(r["total_cents"] or 0),
                    "status": st,
                }
            )
        return jsonify(out)
    finally:
        conn.close()


# -----------------------------
# API: crear pedido
# -----------------------------
@app.post("/api/orders")
def api_create_order():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    address = (data.get("address") or "").strip()
    courier_id = data.get("courier_id")
    payment_method_raw = (data.get("payment_method") or "cash")
    items = data.get("items") or []
    adjustments = data.get("adjustments") or []

    if not phone or not address:
        return jsonify({"ok": False, "error": "Faltan datos: teléfono y dirección."}), 400
    if not isinstance(items, list) or len(items) == 0:
        return jsonify({"ok": False, "error": "El pedido está vacío."}), 400

    conn = db()
    cur = conn.cursor()

    try:
        created_at = datetime.now().isoformat(timespec="seconds")

        # Normalizamos SIEMPRE a los 2 valores soportados por el sistema
        pm = str(payment_method_raw).strip().lower()
        pm_norm = "transfer" if pm in {"transfer", "transferencia"} or pm.startswith("transf") or "transfer" in pm else "cash"

        # Compat: si el schema no tuviera payment_method (versiones viejas), insertamos sin esa columna
        has_payment = _table_has_column(conn, "orders", "payment_method")
        if has_payment:
            cur.execute(
                "INSERT INTO orders(created_at, status, phone, address, courier_id, payment_method, total_cents) "
                "VALUES(?, 'new', ?, ?, ?, ?, 0);",
                (created_at, phone, address, courier_id, pm_norm),
            )
        else:
            cur.execute(
                "INSERT INTO orders(created_at, status, phone, address, courier_id, total_cents) "
                "VALUES(?, 'new', ?, ?, ?, 0);",
                (created_at, phone, address, courier_id),
            )
        order_id = int(cur.lastrowid)

        total_cents = _insert_order_items(conn, cur, order_id, items)

        # Ajustes (envío, descuentos: pueden sumar o restar)
        total_cents += _apply_order_adjustments(conn, cur, order_id, adjustments)

        cur.execute("UPDATE orders SET total_cents=? WHERE id=?;", (total_cents, order_id))
        conn.commit()
        return jsonify({"ok": True, "id": order_id})

    except sqlite3.Error as e:
        conn.rollback()
        return jsonify({"ok": False, "error": f"Error de base de datos: {e}"}), 500
    finally:
        conn.close()


# -----------------------------
# API: obtener pedido (para editar)
# -----------------------------
@app.get("/api/orders/<int:order_id>")
def api_get_order(order_id: int):
    conn = db()
    try:
        o = conn.execute(
            "SELECT * FROM orders WHERE id=?;",
            (order_id,),
        ).fetchone()
        if not o:
            return jsonify({"ok": False, "error": "Pedido no encontrado."}), 404

        items = conn.execute(
            "SELECT * FROM order_items WHERE order_id=? ORDER BY id ASC;",
            (order_id,),
        ).fetchall()

        mods = conn.execute(
            "SELECT order_item_id, kind, ingredient_id FROM order_item_mods "
            "WHERE order_item_id IN (SELECT id FROM order_items WHERE order_id=?) "
            "ORDER BY id ASC;",
            (order_id,),
        ).fetchall()

        mods_by_item: Dict[int, Dict[str, List[int]]] = {}
        for m in mods:
            iid = int(m["order_item_id"])
            mods_by_item.setdefault(iid, {"remove": [], "add": []})
            kind = (m["kind"] or "").strip()
            ing_id = int(m["ingredient_id"] or 0)
            if kind == "remove":
                mods_by_item[iid]["remove"].append(ing_id)
            elif kind == "add":
                # mantenemos repetidos para representar cantidad de extras
                mods_by_item[iid]["add"].append(ing_id)

        pm = "cash"
        if "payment_method" in o.keys() and o["payment_method"]:
            pms = str(o["payment_method"]).strip().lower()
            pm = "transfer" if (pms in {"transfer", "transferencia"} or pms.startswith("transf") or "transfer" in pms) else "cash"

        def _it_field(it, key, default=None):
            return it[key] if key in it.keys() and it[key] is not None else default

        out_items = []
        for it in items:
            pg = _it_field(it, "promo_group")
            out_items.append({
                "id": int(it["id"]),
                "product_id": int(it["product_id"]),
                "qty": int(it["qty"] or 1),
                "removed_ingredient_ids": mods_by_item.get(int(it["id"]), {}).get("remove", []),
                "added_ingredient_ids": mods_by_item.get(int(it["id"]), {}).get("add", []),
                "promo_group": (str(pg) if pg else None),
                "promo_id": int(_it_field(it, "promo_id", 0) or 0),
                "promo_name": _it_field(it, "promo_name_snapshot", "") or "",
                "promo_price_cents": int(_it_field(it, "promo_price_cents", 0) or 0),
            })

        # Ajustes del pedido
        out_adjustments = []
        if _table_has_column(conn, "order_adjustments", "order_id"):
            adj_rows = conn.execute(
                "SELECT label, amount_cents FROM order_adjustments WHERE order_id=? ORDER BY sort_order ASC, id ASC;",
                (order_id,),
            ).fetchall()
            out_adjustments = [
                {"label": r["label"], "amount_cents": int(r["amount_cents"] or 0)} for r in adj_rows
            ]

        return jsonify(
            {
                "ok": True,
                "order": {
                    "id": int(o["id"]),
                    "phone": o["phone"],
                    "address": o["address"],
                    "courier_id": o["courier_id"],
                    "payment_method": pm,
                },
                "items": out_items,
                "adjustments": out_adjustments,
            }
        )
    finally:
        conn.close()


# -----------------------------
# API: actualizar pedido (reemplaza datos + ítems)
# -----------------------------
@app.post("/api/orders/<int:order_id>/update")
def api_update_order(order_id: int):
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    address = (data.get("address") or "").strip()
    courier_id = data.get("courier_id")
    payment_method_raw = (data.get("payment_method") or "cash")
    items = data.get("items") or []
    adjustments = data.get("adjustments") or []

    if not phone or not address:
        return jsonify({"ok": False, "error": "Faltan datos: teléfono y dirección."}), 400
    if not isinstance(items, list) or len(items) == 0:
        return jsonify({"ok": False, "error": "El pedido está vacío."}), 400

    conn = db()
    cur = conn.cursor()
    try:
        exists = conn.execute("SELECT id FROM orders WHERE id=?;", (order_id,)).fetchone()
        if not exists:
            return jsonify({"ok": False, "error": "Pedido no encontrado."}), 404

        # Normalizamos SIEMPRE a los 2 valores soportados por el sistema
        pm = str(payment_method_raw).strip().lower()
        pm_norm = "transfer" if pm in {"transfer", "transferencia"} or pm.startswith("transf") or "transfer" in pm else "cash"

        has_payment = _table_has_column(conn, "orders", "payment_method")
        if has_payment:
            cur.execute(
                "UPDATE orders SET phone=?, address=?, courier_id=?, payment_method=? WHERE id=?;",
                (phone, address, courier_id, pm_norm, order_id),
            )
        else:
            cur.execute(
                "UPDATE orders SET phone=?, address=?, courier_id=? WHERE id=?;",
                (phone, address, courier_id, order_id),
            )

        # Borramos ítems, mods y ajustes previos
        cur.execute(
            "DELETE FROM order_item_mods WHERE order_item_id IN (SELECT id FROM order_items WHERE order_id=?);",
            (order_id,),
        )
        cur.execute("DELETE FROM order_items WHERE order_id=?;", (order_id,))
        if _table_has_column(conn, "order_adjustments", "order_id"):
            cur.execute("DELETE FROM order_adjustments WHERE order_id=?;", (order_id,))

        total_cents = _insert_order_items(conn, cur, order_id, items)

        # Ajustes (envío, descuentos: pueden sumar o restar)
        total_cents += _apply_order_adjustments(conn, cur, order_id, adjustments)

        cur.execute("UPDATE orders SET total_cents=? WHERE id=?;", (total_cents, order_id))
        conn.commit()
        return jsonify({"ok": True})

    except sqlite3.Error as e:
        conn.rollback()
        return jsonify({"ok": False, "error": f"Error de base de datos: {e}"}), 500
    finally:
        conn.close()


# -----------------------------
# API: actualizar estado
# -----------------------------
@app.post("/api/orders/<int:order_id>/status")
def api_update_status(order_id: int):
    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip()
    if status not in {"new", "kitchen", "way", "done"}:
        return jsonify({"ok": False, "error": "Estado inválido."}), 400

    conn = db()
    try:
        conn.execute("UPDATE orders SET status=? WHERE id=?;", (status, order_id))
        conn.commit()
        return jsonify({"ok": True})
    except sqlite3.Error as e:
        conn.rollback()
        return jsonify({"ok": False, "error": f"Error de base de datos: {e}"}), 500
    finally:
        conn.close()


@app.post("/api/orders/<int:order_id>/delete")
def api_delete_order(order_id: int):
    conn = db()
    try:
        # Limpieza explícita (las FK con CASCADE no están forzadas en SQLite por defecto).
        conn.execute(
            "DELETE FROM order_item_mods WHERE order_item_id IN (SELECT id FROM order_items WHERE order_id=?);",
            (order_id,),
        )
        conn.execute("DELETE FROM order_items WHERE order_id=?;", (order_id,))
        if _table_has_column(conn, "order_adjustments", "order_id"):
            conn.execute("DELETE FROM order_adjustments WHERE order_id=?;", (order_id,))
        conn.execute("DELETE FROM orders WHERE id=?;", (order_id,))
        conn.commit()
        return jsonify({"ok": True})
    except sqlite3.Error as e:
        conn.rollback()
        return jsonify({"ok": False, "error": f"Error de base de datos: {e}"}), 500
    finally:
        conn.close()


# -----------------------------
# Ticket
# -----------------------------
@app.get("/orders/<int:order_id>/ticket")
def order_ticket(order_id: int):
    conn = db()

    o = conn.execute(
        "SELECT o.*, c.name AS courier_name "
        "FROM orders o "
        "LEFT JOIN couriers c ON c.id = o.courier_id "
        "WHERE o.id=?;",
        (order_id,),
    ).fetchone()

    if not o:
        conn.close()
        abort(404)

    items = conn.execute(
        "SELECT * FROM order_items WHERE order_id=? ORDER BY id ASC;",
        (order_id,),
    ).fetchall()

    mods = conn.execute(
        "SELECT * FROM order_item_mods "
        "WHERE order_item_id IN (SELECT id FROM order_items WHERE order_id=?) "
        "ORDER BY id ASC;",
        (order_id,),
    ).fetchall()

    adjustments = []
    if _table_has_column(conn, "order_adjustments", "order_id"):
        adj_rows = conn.execute(
            "SELECT label, amount_cents FROM order_adjustments WHERE order_id=? ORDER BY sort_order ASC, id ASC;",
            (order_id,),
        ).fetchall()
        adjustments = [
            {"label": r["label"], "amount_cents": int(r["amount_cents"] or 0)} for r in adj_rows
        ]

    conn.close()

    mods_by_item: Dict[int, List[sqlite3.Row]] = {}
    for m in mods:
        mods_by_item.setdefault(int(m["order_item_id"]), []).append(m)

    dt = fmt_dt(o["created_at"])

    def item_name(row: sqlite3.Row) -> str:
        keys = set(row.keys())
        if "product_name_snapshot" in keys and row["product_name_snapshot"]:
            return row["product_name_snapshot"]
        if "name_snapshot" in keys and row["name_snapshot"]:
            return row["name_snapshot"]
        return "Producto"

    # Armamos "bloques": ítems sueltos y promos (que agrupan varios componentes).
    blocks: List[Dict[str, Any]] = []
    promo_block_by_group: Dict[str, Dict[str, Any]] = {}

    for it in items:
        iid = int(it["id"])
        qty = int(it["qty"] or 1)
        keys = set(it.keys())

        # base price compat
        if "product_price_cents_snapshot" in keys and it["product_price_cents_snapshot"] is not None:
            base_cents = int(it["product_price_cents_snapshot"] or 0)
        elif "base_price_cents" in keys and it["base_price_cents"] is not None:
            base_cents = int(it["base_price_cents"] or 0)
        else:
            base_cents = 0

        removed = []
        added_map: Dict[int, Dict[str, Any]] = {}
        added_total_cents = 0

        for m in mods_by_item.get(iid, []):
            kind = (m["kind"] or "").strip()
            ing_id = int(m["ingredient_id"] or 0)
            if kind == "remove":
                removed.append(m["name_snapshot"])
            elif kind == "add":
                price = int(m["price_cents"] or 0)
                added_total_cents += price
                if ing_id not in added_map:
                    added_map[ing_id] = {
                        "id": ing_id,
                        "name": m["name_snapshot"],
                        "count": 1,
                        "price_cents_total": price,
                    }
                else:
                    added_map[ing_id]["count"] += 1
                    added_map[ing_id]["price_cents_total"] += price

        added = list(added_map.values())
        line_total_cents = (base_cents + added_total_cents) * qty

        item_dict = {
            "id": iid,
            "name": item_name(it),
            "qty": qty,
            "base_price_cents": base_cents,
            "line_total_cents": line_total_cents,
            "removed": removed,
            "added": added,
        }

        promo_group = it["promo_group"] if "promo_group" in keys and it["promo_group"] else None
        if promo_group:
            promo_price = int(it["promo_price_cents"] or 0) if "promo_price_cents" in keys else 0
            promo_name = (it["promo_name_snapshot"] if "promo_name_snapshot" in keys and it["promo_name_snapshot"] else "Promo")
            if promo_group not in promo_block_by_group:
                block = {
                    "kind": "promo",
                    "promo_name": promo_name,
                    "promo_price_cents": 0,
                    "extras_cents": 0,
                    "components": [],
                }
                promo_block_by_group[promo_group] = block
                blocks.append(block)
            blk = promo_block_by_group[promo_group]
            blk["components"].append(item_dict)
            blk["extras_cents"] += added_total_cents * qty
            if promo_price:
                blk["promo_price_cents"] = promo_price  # precio base de la promo (en el ítem ancla)
        else:
            item_dict["kind"] = "item"
            blocks.append(item_dict)

    # Precio mostrado de cada promo = precio base + extras de sus componentes.
    for blk in blocks:
        if blk.get("kind") == "promo":
            blk["line_total_cents"] = int(blk.get("promo_price_cents", 0)) + int(blk.get("extras_cents", 0))

    # Cantidad de copias a imprimir (configurable; se puede forzar por querystring).
    try:
        copies = int(request.args.get("copies") or 0)
    except Exception:
        copies = 0
    if copies <= 0:
        conn2 = db()
        try:
            copies = int(get_setting(conn2, "ticket_copies", "1") or "1")
        except Exception:
            copies = 1
        finally:
            conn2.close()
    copies = max(1, min(copies, 10))

    auto = (request.args.get("autoprint") in ("1", "true", "yes"))

    return render_template(
        "ticket.html",
        order={
            "id": int(o["id"]),
            "ddmm": dt["date"],
            "hhmm": dt["time"],
            "phone": o["phone"],
            "address": o["address"],
            "courier_name": o["courier_name"],
            # 🔧 IMPORTANTE: el template decide "Efectivo/Transferencia" con este campo.
            # Si no lo pasamos, siempre cae en efectivo.
            "payment_method": (
                "transfer"
                if (str(o["payment_method"]) if "payment_method" in o.keys() else "cash").strip().lower() in {"transfer", "transferencia"}
                or (str(o["payment_method"]) if "payment_method" in o.keys() else "cash").strip().lower().startswith("transf")
                or "transfer" in (str(o["payment_method"]) if "payment_method" in o.keys() else "cash").strip().lower()
                else "cash"
            ),
            "total_cents": int(o["total_cents"] or 0),
        },
        blocks=blocks,
        adjustments=adjustments,
        copies=copies,
        autoprint=auto,
    )


# -----------------------------
# Admin login/logout
# -----------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        pw = (request.form.get("password") or "").strip()
        if pw == ADMIN_PASSWORD:
            session[SESSION_ADMIN_KEY] = True
            return redirect(url_for("admin_products"))
        return render_template("admin_login.html", error="Contraseña incorrecta.")
    return render_template("admin_login.html", error=None)


@app.get("/admin/logout")
def admin_logout():
    session.pop(SESSION_ADMIN_KEY, None)
    return redirect(url_for("index"))


# -----------------------------
# Admin: Productos
# -----------------------------
@app.get("/admin")
def admin_home():
    _r = require_admin()
    if _r is not None:
        return _r
    return redirect(url_for("admin_products"))


@app.get("/admin/products")
def admin_products():
    _r = require_admin()
    if _r is not None:
        return _r

    conn = db()
    try:
        ingredients = conn.execute(
            "SELECT id, name, extra_price_cents, active FROM ingredients ORDER BY active DESC, name COLLATE NOCASE ASC;"
        ).fetchall()

        products = conn.execute(
            "SELECT id, name, price_cents, active FROM products ORDER BY active DESC, name COLLATE NOCASE ASC;"
        ).fetchall()

        base = conn.execute("SELECT product_id, ingredient_id FROM product_base_ingredients;").fetchall()
        base_map: Dict[int, List[int]] = {}
        for r in base:
            base_map.setdefault(int(r["product_id"]), []).append(int(r["ingredient_id"]))

        return render_template(
            "admin_products.html",
            products=products,
            ingredients=ingredients,
            base_map=base_map,
        )
    finally:
        conn.close()


@app.post("/admin/products/create")
def admin_products_create():
    _r = require_admin()
    if _r is not None:
        return _r

    name = (request.form.get("name") or "").strip()
    price = (request.form.get("price") or "0").strip()
    base_ings = request.form.getlist("base_ingredients")

    try:
        price_cents = int(float(price) * 100)
    except Exception:
        price_cents = 0

    if not name:
        return redirect(url_for("admin_products"))

    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO products(name, price_cents, active) VALUES(?, ?, 1);",
            (name, price_cents),
        )
        pid = int(cur.lastrowid)

        for iid in base_ings:
            try:
                iid_i = int(iid)
            except Exception:
                continue
            cur.execute(
                "INSERT INTO product_base_ingredients(product_id, ingredient_id) VALUES(?, ?);",
                (pid, iid_i),
            )

        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for("admin_products"))


@app.post("/admin/products/<int:pid>/update")
def admin_products_update(pid: int):
    _r = require_admin()
    if _r is not None:
        return _r

    name = (request.form.get("name") or "").strip()
    price = (request.form.get("price") or "0").strip()
    active = (request.form.get("active") or "1").strip()
    base_ings = request.form.getlist("base_ingredients")

    try:
        price_cents = int(float(price) * 100)
    except Exception:
        price_cents = 0

    active_i = 1 if active == "1" else 0

    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE products SET name=?, price_cents=?, active=? WHERE id=?;",
            (name, price_cents, active_i, pid),
        )
        cur.execute("DELETE FROM product_base_ingredients WHERE product_id=?;", (pid,))
        for iid in base_ings:
            try:
                iid_i = int(iid)
            except Exception:
                continue
            cur.execute(
                "INSERT INTO product_base_ingredients(product_id, ingredient_id) VALUES(?, ?);",
                (pid, iid_i),
            )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for("admin_products"))


@app.post("/admin/products/<int:pid>/delete")
def admin_products_delete(pid: int):
    _r = require_admin()
    if _r is not None:
        return _r

    conn = db()
    try:
        conn.execute("DELETE FROM products WHERE id=?;", (pid,))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        # Si no se puede borrar por historial, lo dejamos inactivo
        try:
            conn.execute("UPDATE products SET active=0 WHERE id=?;", (pid,))
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
    finally:
        conn.close()

    return redirect(url_for("admin_products"))


# -----------------------------
# Admin: Promos (combos)
# -----------------------------
@app.get("/admin/promos")
def admin_promos():
    _r = require_admin()
    if _r is not None:
        return _r

    conn = db()
    try:
        products = conn.execute(
            "SELECT id, name, price_cents, active FROM products ORDER BY active DESC, name COLLATE NOCASE ASC;"
        ).fetchall()
        promos = conn.execute(
            "SELECT id, name, price_cents, active FROM promos ORDER BY active DESC, name COLLATE NOCASE ASC;"
        ).fetchall()
        comp = conn.execute(
            "SELECT promo_id, product_id FROM promo_components ORDER BY sort_order ASC, id ASC;"
        ).fetchall()
        comp_map: Dict[int, List[int]] = {}
        for r in comp:
            comp_map.setdefault(int(r["promo_id"]), []).append(int(r["product_id"]))
        return render_template(
            "admin_promos.html",
            products=products,
            promos=promos,
            comp_map=comp_map,
        )
    finally:
        conn.close()


def _save_promo_components(cur: sqlite3.Cursor, promo_id: int, product_ids: List[Any]) -> None:
    cur.execute("DELETE FROM promo_components WHERE promo_id=?;", (promo_id,))
    sort_i = 0
    for raw in product_ids:
        try:
            pid_i = int(raw)
        except Exception:
            continue
        cur.execute(
            "INSERT INTO promo_components(promo_id, product_id, sort_order) VALUES(?, ?, ?);",
            (promo_id, pid_i, sort_i),
        )
        sort_i += 1


def _promo_components_from_form(form) -> List[int]:
    """Lee los campos 'qty_<product_id>' del form y devuelve la lista de product_ids
    repetidos según la cantidad (ej: Duki x2 -> [id_duki, id_duki])."""
    out: List[int] = []
    for key in form.keys():
        if not key.startswith("qty_"):
            continue
        try:
            pid = int(key[4:])
            n = int(form.get(key) or 0)
        except Exception:
            continue
        n = max(0, min(n, 20))
        out.extend([pid] * n)
    return out


@app.post("/admin/promos/create")
def admin_promos_create():
    _r = require_admin()
    if _r is not None:
        return _r

    name = (request.form.get("name") or "").strip()
    price_cents = _parse_pesos_to_cents(request.form.get("price"), 0)
    components = _promo_components_from_form(request.form)

    if not name:
        return redirect(url_for("admin_promos"))

    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO promos(name, price_cents, active) VALUES(?, ?, 1);",
            (name, price_cents),
        )
        promo_id = int(cur.lastrowid)
        _save_promo_components(cur, promo_id, components)
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for("admin_promos"))


@app.post("/admin/promos/<int:promo_id>/update")
def admin_promos_update(promo_id: int):
    _r = require_admin()
    if _r is not None:
        return _r

    name = (request.form.get("name") or "").strip()
    price_cents = _parse_pesos_to_cents(request.form.get("price"), 0)
    active = (request.form.get("active") or "1").strip()
    active_i = 1 if active == "1" else 0
    components = _promo_components_from_form(request.form)

    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE promos SET name=?, price_cents=?, active=? WHERE id=?;",
            (name, price_cents, active_i, promo_id),
        )
        _save_promo_components(cur, promo_id, components)
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for("admin_promos"))


@app.post("/admin/promos/<int:promo_id>/delete")
def admin_promos_delete(promo_id: int):
    _r = require_admin()
    if _r is not None:
        return _r

    conn = db()
    try:
        conn.execute("DELETE FROM promo_components WHERE promo_id=?;", (promo_id,))
        conn.execute("DELETE FROM promos WHERE id=?;", (promo_id,))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        try:
            conn.execute("UPDATE promos SET active=0 WHERE id=?;", (promo_id,))
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
    finally:
        conn.close()

    return redirect(url_for("admin_promos"))


# -----------------------------
# Admin: Ingredientes
# -----------------------------
@app.get("/admin/ingredients")
def admin_ingredients():
    _r = require_admin()
    if _r is not None:
        return _r

    conn = db()
    try:
        ingredients = conn.execute(
            "SELECT id, name, extra_price_cents, active FROM ingredients ORDER BY active DESC, name COLLATE NOCASE ASC;"
        ).fetchall()
        return render_template("admin_ingredients.html", ingredients=ingredients)
    finally:
        conn.close()


@app.post("/admin/ingredients/create")
def admin_ingredients_create():
    _r = require_admin()
    if _r is not None:
        return _r

    name = (request.form.get("name") or "").strip()
    extra_price = (request.form.get("extra_price") or "0").strip()

    try:
        extra_cents = int(float(extra_price) * 100)
    except Exception:
        extra_cents = 0

    if not name:
        return redirect(url_for("admin_ingredients"))

    conn = db()
    try:
        conn.execute(
            "INSERT INTO ingredients(name, extra_price_cents, active) VALUES(?, ?, 1);",
            (name, extra_cents),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for("admin_ingredients"))


@app.post("/admin/ingredients/<int:iid>/update")
def admin_ingredients_update(iid: int):
    _r = require_admin()
    if _r is not None:
        return _r

    name = (request.form.get("name") or "").strip()
    extra_price = (request.form.get("extra_price") or "0").strip()

    try:
        extra_cents = int(float(extra_price) * 100)
    except Exception:
        extra_cents = 0

    conn = db()
    try:
        conn.execute(
            "UPDATE ingredients SET name=?, extra_price_cents=? WHERE id=?;",
            (name, extra_cents, iid),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for("admin_ingredients"))


@app.post("/admin/ingredients/<int:iid>/delete")
def admin_ingredients_delete(iid: int):
    _r = require_admin()
    if _r is not None:
        return _r

    conn = db()
    try:
        conn.execute("DELETE FROM ingredients WHERE id=?;", (iid,))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for("admin_ingredients"))


# -----------------------------
# Admin: Repartidores
# -----------------------------
@app.get("/admin/couriers")
def admin_couriers():
    _r = require_admin()
    if _r is not None:
        return _r

    conn = db()
    try:
        has_float = _table_has_column(conn, "couriers", "cash_float_cents")
        if has_float:
            couriers = conn.execute(
                "SELECT id, name, cash_float_cents FROM couriers ORDER BY name COLLATE NOCASE ASC;"
            ).fetchall()
        else:
            couriers = conn.execute(
                "SELECT id, name FROM couriers ORDER BY name COLLATE NOCASE ASC;"
            ).fetchall()
        return render_template(
            "admin_couriers.html",
            couriers=couriers,
            default_float=money(CASH_FLOAT_CENTS),
        )
    finally:
        conn.close()


@app.post("/admin/couriers/create")
def admin_couriers_create():
    _r = require_admin()
    if _r is not None:
        return _r

    name = (request.form.get("name") or "").strip()
    if not name:
        return redirect(url_for("admin_couriers"))

    float_cents = _parse_pesos_to_cents(request.form.get("cash_float"), CASH_FLOAT_CENTS)

    conn = db()
    try:
        if _table_has_column(conn, "couriers", "cash_float_cents"):
            conn.execute(
                "INSERT INTO couriers(name, cash_float_cents) VALUES(?, ?);",
                (name, float_cents),
            )
        else:
            conn.execute("INSERT INTO couriers(name) VALUES(?);", (name,))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for("admin_couriers"))


@app.post("/admin/couriers/<int:cid>/update")
def admin_couriers_update(cid: int):
    _r = require_admin()
    if _r is not None:
        return _r

    name = (request.form.get("name") or "").strip()
    float_cents = _parse_pesos_to_cents(request.form.get("cash_float"), CASH_FLOAT_CENTS)

    conn = db()
    try:
        if _table_has_column(conn, "couriers", "cash_float_cents"):
            conn.execute(
                "UPDATE couriers SET name=?, cash_float_cents=? WHERE id=?;",
                (name, float_cents, cid),
            )
        else:
            conn.execute("UPDATE couriers SET name=? WHERE id=?;", (name, cid))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for("admin_couriers"))


@app.post("/admin/couriers/<int:cid>/delete")
def admin_couriers_delete(cid: int):
    _r = require_admin()
    if _r is not None:
        return _r

    conn = db()
    try:
        conn.execute("DELETE FROM couriers WHERE id=?;", (cid,))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for("admin_couriers"))


# -----------------------------
# Admin: limpiar pedidos
# -----------------------------
@app.post("/admin/clear_orders")
def admin_clear_orders():
    _r = require_admin()
    if _r is not None:
        return _r

    pw = (request.form.get("password") or "").strip()
    if pw != ADMIN_PASSWORD:
        return redirect(url_for("admin_products"))

    conn = db()
    try:
        # 1) Generar PDF ANTES de borrar (incluye TODOS los pedidos vivos)
        filename, pdf_bytes = generate_daily_orders_pdf_bytes(conn)

        # 2) Borrar pedidos y sus tablas hijas (las FK CASCADE no están forzadas en SQLite,
        #    así que limpiamos a mano para no dejar ítems/mods/ajustes huérfanos).
        conn.execute("DELETE FROM order_item_mods;")
        conn.execute("DELETE FROM order_items;")
        if _table_has_column(conn, "order_adjustments", "order_id"):
            conn.execute("DELETE FROM order_adjustments;")
        conn.execute("DELETE FROM orders;")
        conn.commit()

        # 3) Descargar PDF
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )

    except sqlite3.Error:
        conn.rollback()
        return redirect(url_for("admin_products"))
    finally:
        conn.close()



# -----------------------------
# Liquidación
# -----------------------------
@app.get("/api/liquidation")
def api_liquidation():
    """
    Corrige cálculo de transferencias en liquidación.
    - No toca ningún otro comportamiento ni el diseño.
    - Las transferencias se restan del total (efectivo a rendir).
    - Detecta cualquier forma de escribir transferencia.
    """
    conn = db()
    try:
        has_payment = _table_has_column(conn, "orders", "payment_method")

        # --- Totales globales ---
        total_cents = 0
        transfer_cents = 0

        if has_payment:
            rows = conn.execute("SELECT total_cents, payment_method FROM orders;").fetchall()
            for r in rows:
                total_cents += int(r["total_cents"] or 0)
                pm = (r["payment_method"] or "").strip().lower()
                if pm.startswith("transf") or "transfer" in pm or "banco" in pm or "mp" in pm or "mercado" in pm:
                    transfer_cents += int(r["total_cents"] or 0)
        else:
            total_row = conn.execute("SELECT COALESCE(SUM(total_cents),0) AS total FROM orders;").fetchone()
            total_cents = int(total_row["total"] or 0)

        # Base sin cambios (lo que dice la fórmula "Total - Transferencia")
        cash_base_cents = total_cents - transfer_cents

        # El cambio ($) ahora es configurable por repartidor (couriers.cash_float_cents).
        has_float = _table_has_column(conn, "couriers", "cash_float_cents")
        float_sel = "c.cash_float_cents AS cash_float_cents," if has_float else ""

        # --- Por repartidor ---
        if has_payment:
            orders = conn.execute(f"""
                SELECT c.id AS courier_id, c.name AS courier_name, {float_sel}
                       o.total_cents, o.payment_method
                FROM couriers c
                JOIN orders o ON o.courier_id = c.id;
            """).fetchall()
        else:
            orders = conn.execute(f"""
                SELECT c.id AS courier_id, c.name AS courier_name, {float_sel}
                       o.total_cents
                FROM couriers c
                JOIN orders o ON o.courier_id = c.id;
            """).fetchall()

        data = {}
        for o in orders:
            cid = int(o["courier_id"])
            name = o["courier_name"]
            if cid not in data:
                if has_float and o["cash_float_cents"] is not None:
                    cfloat = int(o["cash_float_cents"])
                else:
                    cfloat = CASH_FLOAT_CENTS
                data[cid] = {
                    "courier_id": cid,
                    "courier_name": name,
                    "orders_count": 0,
                    "total_cents": 0,
                    "transfer_cents": 0,
                    "cash_cents": 0,
                    "cash_float_cents": cfloat,
                }

            data[cid]["orders_count"] += 1
            data[cid]["total_cents"] += int(o["total_cents"] or 0)

            if has_payment:
                pm = (o["payment_method"] or "").strip().lower()
                if pm.startswith("transf") or "transfer" in pm or "banco" in pm or "mp" in pm or "mercado" in pm:
                    data[cid]["transfer_cents"] += int(o["total_cents"] or 0)

        cash_float_total_cents = 0
        for d in data.values():
            # Total efectivo a rendir por repartidor = (total - transferencias) + su cambio
            d["cash_cents"] = d["total_cents"] - d["transfer_cents"]
            # +cambio una sola vez si el repartidor apareció (tiene pedidos)
            if d["orders_count"] > 0:
                d["cash_cents"] += d["cash_float_cents"]
                cash_float_total_cents += d["cash_float_cents"]
            # Para que el "TOTAL" del repartidor muestre (efectivo - transf + cambio)
            # sin tocar el detalle de abajo (que sigue mostrando efectivo y transf),
            # preservamos el total bruto en un campo aparte.
            d["gross_total_cents"] = d["total_cents"]
            d["total_cents"] = d["cash_cents"]

        # El efectivo a rendir global = base (Total - Transferencia) + suma de cambios
        cash_to_render_cents = cash_base_cents + cash_float_total_cents

        # --- Armar respuesta ---
        return jsonify({
            "total_cents": total_cents,
            "transfer_cents": transfer_cents,
            "cash_base_cents": cash_base_cents,
            "cash_float_total_cents": cash_float_total_cents,
            "cash_to_render_cents": cash_to_render_cents,
            "total": money(total_cents),
            "transfer": money(transfer_cents),
            "cash_base": money(cash_base_cents),
            "cash_float_total": money(cash_float_total_cents),
            "cash_to_render": money(cash_to_render_cents),
            "couriers": [
                {
                    **d,
                    # "total" = efectivo a rendir (cash)
                    "total": money(d["total_cents"]),
                    # total bruto (incluye transferencias), por si el front lo quiere mostrar/usar
                    "gross_total": money(d.get("gross_total_cents", 0)),
                    "transfer": money(d["transfer_cents"]),
                    "cash": money(d["cash_cents"]),
                    "cash_float": money(d.get("cash_float_cents", 0)),
                }
                for d in data.values()
            ]
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


# Migraciones idempotentes al cargar el módulo (launcher o ejecución directa).
# Si algo fallara, no debe impedir el arranque del servidor.
try:
    ensure_schema()
except Exception:
    pass


if __name__ == "__main__":
    app.run(debug=True)
