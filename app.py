# (archivo completo)
from __future__ import annotations

import os
import sqlite3
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
DB_PATH = os.path.join(APP_DIR, "magikburger.db")

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


def money(cents: int) -> int:
    return int(round((cents or 0) / 100))


def fmt_dt(iso: str) -> Dict[str, str]:
    try:
        dt = datetime.fromisoformat(iso)
    except Exception:
        dt = datetime.now()
    return {"date": dt.strftime("%d/%m"), "time": dt.strftime("%H:%M")}


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
            }
        )
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

        prod_rows = conn.execute("SELECT id, name, price_cents FROM products WHERE active=1;").fetchall()
        prod_map = {int(p["id"]): p for p in prod_rows}

        ing_rows = conn.execute("SELECT id, name, extra_price_cents FROM ingredients WHERE active=1;").fetchall()
        ing_map = {int(i["id"]): i for i in ing_rows}

        # ✅ Compatibilidad con ambas versiones de schema
        has_name_old = _table_has_column(conn, "order_items", "name_snapshot")
        has_name_new = _table_has_column(conn, "order_items", "product_name_snapshot")
        has_price_old = _table_has_column(conn, "order_items", "base_price_cents")
        has_price_new = _table_has_column(conn, "order_items", "product_price_cents_snapshot")

        total_cents = 0

        for it in items:
            pid = int(it.get("product_id") or 0)
            if pid not in prod_map:
                continue

            qty = int(it.get("qty") or 1)
            if qty < 1:
                qty = 1

            p = prod_map[pid]
            base_price = int(p["price_cents"])

            cols = ["order_id", "product_id", "qty"]
            vals: List[Any] = [order_id, pid, qty]

            # nombre snapshot (viejo/nuevo)
            if has_name_old:
                cols.append("name_snapshot")
                vals.append(p["name"])
            if has_name_new:
                cols.append("product_name_snapshot")
                vals.append(p["name"])

            # precio snapshot (viejo/nuevo)
            if has_price_old:
                cols.append("base_price_cents")
                vals.append(base_price)
            if has_price_new:
                cols.append("product_price_cents_snapshot")
                vals.append(base_price)

            # Si por alguna razón ninguna existe (raro), no seguimos.
            if not (has_name_old or has_name_new):
                raise sqlite3.OperationalError("Schema incompatible: falta name_snapshot/product_name_snapshot en order_items.")
            if not (has_price_old or has_price_new):
                pass

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

            item_total = (base_price + add_total) * qty
            total_cents += item_total

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
            "SELECT id, product_id, qty FROM order_items WHERE order_id=? ORDER BY id ASC;",
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
                "items": [
                    {
                        "id": int(it["id"]),
                        "product_id": int(it["product_id"]),
                        "qty": int(it["qty"] or 1),
                        "removed_ingredient_ids": mods_by_item.get(int(it["id"]), {}).get("remove", []),
                        "added_ingredient_ids": mods_by_item.get(int(it["id"]), {}).get("add", []),
                    }
                    for it in items
                ],
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

        # Borramos ítems y mods previos
        cur.execute(
            "DELETE FROM order_item_mods WHERE order_item_id IN (SELECT id FROM order_items WHERE order_id=?);",
            (order_id,),
        )
        cur.execute("DELETE FROM order_items WHERE order_id=?;", (order_id,))

        prod_rows = conn.execute("SELECT id, name, price_cents FROM products WHERE active=1;").fetchall()
        prod_map = {int(p["id"]): p for p in prod_rows}
        ing_rows = conn.execute("SELECT id, name, extra_price_cents FROM ingredients WHERE active=1;").fetchall()
        ing_map = {int(i["id"]): i for i in ing_rows}

        # Compatibilidad con ambas versiones de schema
        has_name_old = _table_has_column(conn, "order_items", "name_snapshot")
        has_name_new = _table_has_column(conn, "order_items", "product_name_snapshot")
        has_price_old = _table_has_column(conn, "order_items", "base_price_cents")
        has_price_new = _table_has_column(conn, "order_items", "product_price_cents_snapshot")

        total_cents = 0
        for it in items:
            pid = int(it.get("product_id") or 0)
            if pid not in prod_map:
                continue

            qty = int(it.get("qty") or 1)
            if qty < 1:
                qty = 1

            p = prod_map[pid]
            base_price = int(p["price_cents"])

            cols = ["order_id", "product_id", "qty"]
            vals: List[Any] = [order_id, pid, qty]

            if has_name_old:
                cols.append("name_snapshot")
                vals.append(p["name"])
            if has_name_new:
                cols.append("product_name_snapshot")
                vals.append(p["name"])

            if has_price_old:
                cols.append("base_price_cents")
                vals.append(base_price)
            if has_price_new:
                cols.append("product_price_cents_snapshot")
                vals.append(base_price)

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

            item_total = (base_price + add_total) * qty
            total_cents += item_total

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

    out_items = []
    for it in items:
        iid = int(it["id"])
        qty = int(it["qty"] or 1)

        # base price compat
        keys = set(it.keys())
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

        out_items.append({
            "id": iid,
            "name": item_name(it),
            "qty": qty,
            "base_price_cents": base_cents,
            "line_total_cents": line_total_cents,
            "removed": removed,
            "added": added,
        })

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
        items=out_items,
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
        couriers = conn.execute(
            "SELECT id, name FROM couriers ORDER BY name COLLATE NOCASE ASC;"
        ).fetchall()
        return render_template("admin_couriers.html", couriers=couriers)
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

    conn = db()
    try:
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

    conn = db()
    try:
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
        conn.execute("DELETE FROM orders;")
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for("admin_products"))


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

        cash_to_render_cents = total_cents - transfer_cents

        # --- Por repartidor ---
        if has_payment:
            orders = conn.execute("""
                SELECT c.id AS courier_id, c.name AS courier_name, o.total_cents, o.payment_method
                FROM couriers c
                JOIN orders o ON o.courier_id = c.id;
            """).fetchall()
        else:
            orders = conn.execute("""
                SELECT c.id AS courier_id, c.name AS courier_name, o.total_cents
                FROM couriers c
                JOIN orders o ON o.courier_id = c.id;
            """).fetchall()

        data = {}
        for o in orders:
            cid = int(o["courier_id"])
            name = o["courier_name"]
            if cid not in data:
                data[cid] = {
                    "courier_id": cid,
                    "courier_name": name,
                    "orders_count": 0,
                    "total_cents": 0,
                    "transfer_cents": 0,
                    "cash_cents": 0,
                }

            data[cid]["orders_count"] += 1
            data[cid]["total_cents"] += int(o["total_cents"] or 0)

            if has_payment:
                pm = (o["payment_method"] or "").strip().lower()
                if pm.startswith("transf") or "transfer" in pm or "banco" in pm or "mp" in pm or "mercado" in pm:
                    data[cid]["transfer_cents"] += int(o["total_cents"] or 0)

        for d in data.values():
            # Total efectivo a rendir por repartidor = total - transferencias
            d["cash_cents"] = d["total_cents"] - d["transfer_cents"]

            # Para que el "TOTAL" del repartidor muestre (efectivo - transf)
            # sin tocar el detalle de abajo (que sigue mostrando efectivo y transf),
            # preservamos el total bruto en un campo aparte.
            d["gross_total_cents"] = d["total_cents"]
            d["total_cents"] = d["cash_cents"]

        # --- Armar respuesta ---
        return jsonify({
            "total_cents": total_cents,
            "transfer_cents": transfer_cents,
            "cash_to_render_cents": cash_to_render_cents,
            "total": money(total_cents),
            "transfer": money(transfer_cents),
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
                }
                for d in data.values()
            ]
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(debug=True)
