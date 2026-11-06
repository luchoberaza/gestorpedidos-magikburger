"""Mejores clientes: quién pidió más en un mes (o en los últimos meses).

El pedido no guarda el nombre del cliente: lo identifica la dirección. Dos
direcciones cuentan como el mismo cliente si se escribieron igual, sin importar
mayúsculas, tildes, puntos ni espacios de más ("Brasil 1234 Apto. 2" y
"brasil 1234 apto 2" son la misma; "Brasil 1234" y "Brasil 1234 apto 2", no).

Lo comprado es el total del pedido sin el envío (que es del repartidor), igual
que en Ventas y Reportes.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Any, Dict, List, Optional

from .finanzas import meses_atras, pedidos_del_periodo

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
ORDENES = ("pedidos", "plata")
MESES_HACIA_ATRAS = 6  # el mes actual y los 5 anteriores
TODOS = "todos"


def clave_direccion(texto: Any) -> str:
    s = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def periodos(hoy: str) -> List[Dict[str, str]]:
    """Opciones para elegir: cada uno de los últimos meses (el actual primero) y todos juntos."""
    meses = list(reversed(meses_atras(hoy, MESES_HACIA_ATRAS)))
    out = []
    for m in meses:
        anio, mes = int(m["desde"][:4]), int(m["desde"][5:7])
        out.append({"id": m["desde"][:7], "corto": MESES[mes - 1][:3].capitalize(),
                    "etiqueta": f"{MESES[mes - 1].capitalize()} {anio}",
                    "desde": m["desde"], "hasta": m["hasta"]})
    out.append({"id": TODOS, "corto": f"{MESES_HACIA_ATRAS} meses",
                "etiqueta": f"Últimos {MESES_HACIA_ATRAS} meses ({out[-1]['etiqueta']} a {out[0]['etiqueta']})",
                "desde": meses[-1]["desde"], "hasta": meses[0]["hasta"]})
    return out


def _favoritos(conn: sqlite3.Connection, ids: Dict[str, List[int]]) -> Dict[str, Dict[str, Any]]:
    """El producto que más pidió cada cliente (una promo cuenta una vez por pedido)."""
    todos = [i for lista in ids.values() for i in lista]
    if not todos:
        return {}
    cliente_de = {i: k for k, lista in ids.items() for i in lista}
    marcas = ",".join("?" * len(todos))
    filas = conn.execute(
        f"SELECT order_id, qty, COALESCE(product_name_snapshot, name_snapshot) AS nombre, "
        f"promo_group, promo_name_snapshot FROM order_items WHERE order_id IN ({marcas});", todos).fetchall()
    cuenta: Dict[str, Dict[str, int]] = {}
    promos_vistas = set()
    for f in filas:
        k = cliente_de[int(f["order_id"])]
        if f["promo_group"]:
            clave = (f["order_id"], f["promo_group"])
            if clave in promos_vistas:
                continue
            promos_vistas.add(clave)
            nombre, qty = "Promo · " + (f["promo_name_snapshot"] or "Promo"), 1
        else:
            nombre, qty = f["nombre"] or "Producto", int(f["qty"] or 1)
        c = cuenta.setdefault(k, {})
        c[nombre] = c.get(nombre, 0) + qty
    out = {}
    for k, c in cuenta.items():
        nombre, unidades = sorted(c.items(), key=lambda x: (-x[1], x[0]))[0]
        out[k] = {"nombre": nombre, "unidades": unidades}
    return out


def mejores(conn: sqlite3.Connection, desde: str, hasta: str, limite: Optional[int] = 5,
            orden: str = "pedidos") -> Dict[str, Any]:
    """Ranking de clientes del período. `limite` 0 o None = todos."""
    grupos: Dict[str, Dict[str, Any]] = {}
    ids: Dict[str, List[int]] = {}
    for p in pedidos_del_periodo(conn, desde, hasta):  # del más nuevo al más viejo
        k = clave_direccion(p["address"])
        if not k:
            continue
        g = grupos.get(k)
        if g is None:
            g = grupos[k] = {"direccion": "", "formas": {}, "telefonos": [], "pedidos": 0,
                             "total_cents": 0, "ultimo": p["created_at"], "primero": p["created_at"]}
            ids[k] = []
        forma = " ".join(str(p["address"]).split())
        g["formas"][forma] = g["formas"].get(forma, 0) + 1
        g["pedidos"] += 1
        g["total_cents"] += int(p["neto_cents"])
        g["primero"] = p["created_at"]
        tel = str(p["phone"] or "").strip()
        if tel and tel not in g["telefonos"]:
            g["telefonos"].append(tel)
        ids[k].append(int(p["id"]))

    if orden == "plata":
        criterio = lambda kv: (-kv[1]["total_cents"], -kv[1]["pedidos"], kv[0])  # noqa: E731
    else:
        criterio = lambda kv: (-kv[1]["pedidos"], -kv[1]["total_cents"], kv[0])  # noqa: E731
    ordenados = sorted(grupos.items(), key=criterio)
    if limite:
        ordenados = ordenados[:limite]

    favoritos = _favoritos(conn, {k: ids[k] for k, _ in ordenados})
    pedidos_total = sum(g["pedidos"] for g in grupos.values())
    ventas_total = sum(g["total_cents"] for g in grupos.values())
    clientes = []
    for puesto, (k, g) in enumerate(ordenados, start=1):
        formas = g.pop("formas")
        # Se muestra como se escribe más seguido; si empatan, como se escribió la última vez.
        g["direccion"] = max(formas.items(), key=lambda kv: kv[1])[0]
        clientes.append({
            **g, "puesto": puesto, "telefonos": g["telefonos"][:3],
            "ticket_promedio_cents": int(round(g["total_cents"] / g["pedidos"] / 100)) * 100,  # en pesos redondos
            "favorito": favoritos.get(k),
            "parte_pedidos": round(g["pedidos"] / pedidos_total, 4) if pedidos_total else 0,
            "parte_ventas": round(g["total_cents"] / ventas_total, 4) if ventas_total else 0,
        })
    return {"clientes": clientes, "clientes_distintos": len(grupos), "pedidos_total": pedidos_total,
            "ventas_total_cents": ventas_total}
