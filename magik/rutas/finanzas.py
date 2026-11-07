"""Inicio (indicadores), ventas, gastos, pagos fijos, reportes y exportación."""
from __future__ import annotations

import io
from datetime import date, timedelta
from typing import Any, Dict

from flask import Blueprint, request, send_file

from .. import auth, config
from ..db import ahora_iso, get_db
from ..documentos import hojas_reporte, pdf_reporte, xlsx
from ..dominio import clientes
from ..dominio import costos
from ..dominio import finanzas as fin
from ..dominio import liquidacion as liq
from ..dominio import stock
from ..dominio.pedidos import money
from .util import cuerpo, error, fecha, numero, ok, periodo, pesos, texto

bp = Blueprint("finanzas", __name__)


def _hoy() -> str:
    return liq.jornada_actual(get_db())


def _rangos(hoy: str) -> Dict[str, Dict[str, str]]:
    d = date.fromisoformat(hoy)
    lunes = d - timedelta(days=d.weekday())
    return {
        "dia": {"desde": hoy, "hasta": hoy},
        "semana": {"desde": lunes.isoformat(), "hasta": (lunes + timedelta(days=6)).isoformat()},
        "mes": {"desde": d.replace(day=1).isoformat(), "hasta": hoy},
        "anio": {"desde": d.replace(month=1, day=1).isoformat(), "hasta": hoy},
    }


def _stock_bajo(conn: Any) -> list:
    comp = stock.comprometido(conn)
    out = []
    for i in conn.execute("SELECT * FROM insumos WHERE activo=1 ORDER BY nombre;").fetchall():
        disponible = round(float(i["stock"] or 0) - comp.get(int(i["id"]), 0.0), 3)
        if float(i["stock_minimo"] or 0) > 0 and disponible <= float(i["stock_minimo"] or 0):
            out.append({"id": int(i["id"]), "nombre": i["nombre"], "disponible": disponible,
                        "minimo": float(i["stock_minimo"]), "unidad": i["unidad"]})
    return out


@bp.get("/api/notificaciones")
def api_notificaciones():
    """Avisos de la campana, según lo que el usuario puede ver."""
    u = auth.usuario_actual()
    if u is None:
        return ok(avisos=[])
    conn = get_db()
    hoy = liq.jornada_actual(conn)
    avisos = []
    p = u["permisos"]
    if "gastos" in p:
        for r in fin.recordatorios_proximos(conn, hoy, config.obtener(conn, "recordatorio_dias_aviso")):
            cuando = (f"venció hace {-r['dias_restantes']} día(s)" if r["vencido"] else
                      "vence hoy" if r["dias_restantes"] == 0 else f"vence en {r['dias_restantes']} día(s)")
            avisos.append({"tipo": "pago", "nivel": "alerta" if r["vencido"] else "aviso",
                           "titulo": r["servicio"], "detalle": cuando, "ir": "gastos"})
    if "inventario" in p:
        for s in _stock_bajo(conn):
            avisos.append({"tipo": "stock", "nivel": "alerta", "titulo": f"Stock bajo: {s['nombre']}",
                           "detalle": f"Quedan {s['disponible']:g} {s['unidad']} (mínimo {s['minimo']:g})",
                           "ir": "inventario"})
    if "liquidacion" in p:
        c = liq.arrastrados(conn, hoy)
        if c["count"]:
            avisos.append({"tipo": "pedidos", "nivel": "alerta",
                           "titulo": f"{c['count']} pedido(s) de jornadas anteriores sin cerrar",
                           "detalle": "Revisalos en Pedidos o cerrá la jornada.", "ir": "pedidos"})
    return ok(avisos=avisos)


# ---------------------------------------------------------------------------
# Inicio
# ---------------------------------------------------------------------------
@bp.get("/api/dashboard")
@auth.requiere("inicio")
def api_dashboard():
    conn = get_db()
    hoy = _hoy()
    rg = _rangos(hoy)
    d0 = (date.fromisoformat(hoy) - timedelta(days=13)).isoformat()
    catorce = fin.resumen(conn, d0, hoy)
    mes = fin.resumen(conn, rg["mes"]["desde"], rg["mes"]["hasta"])
    dia = fin.resumen(conn, hoy, hoy, detalle=False)
    ayer_d = (date.fromisoformat(hoy) - timedelta(days=1)).isoformat()
    ayer = fin.resumen(conn, ayer_d, ayer_d, detalle=False)
    return ok(
        hoy=hoy, dia=dia, ayer=ayer, mes={k: v for k, v in mes.items() if not isinstance(v, list)},
        serie=catorce["serie_dias"], gastos_por_categoria=mes["gastos_por_categoria"],
        top_productos=mes["top_productos"][:6], pedidos_por_hora=mes["pedidos_por_hora"],
        stock_bajo=_stock_bajo(conn),
        recordatorios=fin.recordatorios_proximos(conn, hoy, config.obtener(conn, "recordatorio_dias_aviso")),
        ultimos_gastos=fin.gastos(conn, None, None)[:6],
    )


# ---------------------------------------------------------------------------
# Ventas
# ---------------------------------------------------------------------------
@bp.get("/api/ventas")
@auth.requiere("ventas")
def api_ventas():
    conn = get_db()
    desde, hasta = periodo()
    medio = (request.args.get("medio") or "").strip()
    pedidos = fin.pedidos_del_periodo(conn, desde, hasta)
    manuales = fin.ventas_manuales(conn, desde, hasta)
    if medio:
        pedidos = [p for p in pedidos if p["medio"] == medio]
        manuales = [m for m in manuales if (m["metodo_pago"] or "") == medio]
    return ok(pedidos=pedidos, manuales=manuales,
              total_pedidos_cents=sum(p["neto_cents"] for p in pedidos),
              total_envios_cents=sum(p["envio_cents"] for p in pedidos),
              total_manuales_cents=sum(int(m["total_cents"]) for m in manuales))


def _datos_venta(d: Dict[str, Any]) -> Any:
    concepto = texto(d, "concepto", 120)
    if not concepto:
        return None, "Escribí qué se vendió."
    cantidad = numero(d, "cantidad", 1.0)
    if cantidad <= 0:
        return None, "La cantidad tiene que ser mayor que cero."
    unit = pesos(d, "precio_unit")
    total = pesos(d, "total", -1)
    if total < 0:
        total = int(round(unit * cantidad))
    if total <= 0:
        return None, "Ingresá el precio o el total de la venta."
    return {"fecha": fecha(d.get("fecha"), _hoy()), "hora": texto(d, "hora", 5), "concepto": concepto,
            "cantidad": cantidad, "precio_unit_cents": unit, "total_cents": total,
            "metodo_pago": texto(d, "metodo_pago", 40), "observaciones": texto(d, "observaciones", 300)}, None


@bp.post("/api/ventas-manuales")
@auth.requiere("ventas")
def api_venta_nueva():
    v, err = _datos_venta(cuerpo())
    if err:
        return error(err)
    conn = get_db()
    u = auth.usuario_actual()
    cur = conn.execute(
        "INSERT INTO ventas_manuales(fecha, hora, concepto, cantidad, precio_unit_cents, total_cents, metodo_pago, "
        "observaciones, created_at, user_id) VALUES(?,?,?,?,?,?,?,?,?,?);",
        (v["fecha"], v["hora"], v["concepto"], v["cantidad"], v["precio_unit_cents"], v["total_cents"],
         v["metodo_pago"], v["observaciones"], ahora_iso(), u["id"] if u else None))
    conn.commit()
    return ok(id=cur.lastrowid)


@bp.put("/api/ventas-manuales/<int:vid>")
@auth.requiere("ventas")
def api_venta_editar(vid: int):
    v, err = _datos_venta(cuerpo())
    if err:
        return error(err)
    conn = get_db()
    n = conn.execute(
        "UPDATE ventas_manuales SET fecha=?, hora=?, concepto=?, cantidad=?, precio_unit_cents=?, total_cents=?, "
        "metodo_pago=?, observaciones=? WHERE id=?;",
        (v["fecha"], v["hora"], v["concepto"], v["cantidad"], v["precio_unit_cents"], v["total_cents"],
         v["metodo_pago"], v["observaciones"], vid)).rowcount
    conn.commit()
    return ok() if n else error("Esa venta ya no existe.", 404)


@bp.delete("/api/ventas-manuales/<int:vid>")
@auth.requiere("ventas")
def api_venta_borrar(vid: int):
    conn = get_db()
    v = conn.execute("SELECT * FROM ventas_manuales WHERE id=?;", (vid,)).fetchone()
    if v is None:
        return error("Esa venta ya no existe.", 404)
    conn.execute("DELETE FROM ventas_manuales WHERE id=?;", (vid,))
    auth.auditar(conn, "Venta manual eliminada", f"{v['fecha']} {v['concepto']} ${money(v['total_cents'])}")
    conn.commit()
    return ok()


# ---------------------------------------------------------------------------
# Gastos
# ---------------------------------------------------------------------------
@bp.get("/api/gastos")
@auth.requiere("gastos")
def api_gastos():
    conn = get_db()
    desde, hasta = periodo()
    cat = (request.args.get("categoria") or "").strip()
    medio = (request.args.get("medio") or "").strip()
    filas = [g for g in fin.gastos(conn, desde, hasta)
             if (not cat or g["categoria"] == cat) and (not medio or g["metodo_pago"] == medio)]
    return ok(gastos=filas, total_cents=sum(int(g["importe_cents"]) for g in filas),
              umbral_cents=config.obtener(conn, "umbral_gasto_cents"))


def _datos_gasto(d: Dict[str, Any]) -> Any:
    desc = texto(d, "descripcion", 160)
    if not desc:
        return None, "Escribí una descripción del gasto."
    imp = pesos(d, "importe")
    if imp <= 0:
        return None, "El importe tiene que ser mayor que cero."
    return {"fecha": fecha(d.get("fecha"), _hoy()), "categoria": texto(d, "categoria", 40), "descripcion": desc,
            "proveedor": texto(d, "proveedor", 80), "importe_cents": imp, "metodo_pago": texto(d, "metodo_pago", 40),
            "observaciones": texto(d, "observaciones", 300)}, None


def _insertar_gasto(conn: Any, g: Dict[str, Any]) -> int:
    u = auth.usuario_actual()
    cur = conn.execute(
        "INSERT INTO gastos(fecha, categoria, descripcion, proveedor, importe_cents, metodo_pago, observaciones, "
        "created_at, user_id) VALUES(?,?,?,?,?,?,?,?,?);",
        (g["fecha"], g["categoria"], g["descripcion"], g["proveedor"], g["importe_cents"], g["metodo_pago"],
         g["observaciones"], ahora_iso(), u["id"] if u else None))
    return int(cur.lastrowid)


@bp.post("/api/gastos")
@auth.requiere("gastos")
def api_gasto_nuevo():
    g, err = _datos_gasto(cuerpo())
    if err:
        return error(err)
    conn = get_db()
    gid = _insertar_gasto(conn, g)
    conn.commit()
    umbral = config.obtener(conn, "umbral_gasto_cents")
    return ok(id=gid, elevado=bool(umbral and g["importe_cents"] > umbral))


@bp.put("/api/gastos/<int:gid>")
@auth.requiere("gastos")
def api_gasto_editar(gid: int):
    g, err = _datos_gasto(cuerpo())
    if err:
        return error(err)
    conn = get_db()
    n = conn.execute(
        "UPDATE gastos SET fecha=?, categoria=?, descripcion=?, proveedor=?, importe_cents=?, metodo_pago=?, "
        "observaciones=? WHERE id=?;",
        (g["fecha"], g["categoria"], g["descripcion"], g["proveedor"], g["importe_cents"], g["metodo_pago"],
         g["observaciones"], gid)).rowcount
    conn.commit()
    return ok() if n else error("Ese gasto ya no existe.", 404)


@bp.delete("/api/gastos/<int:gid>")
@auth.requiere("gastos")
def api_gasto_borrar(gid: int):
    conn = get_db()
    g = conn.execute("SELECT * FROM gastos WHERE id=?;", (gid,)).fetchone()
    if g is None:
        return error("Ese gasto ya no existe.", 404)
    conn.execute("DELETE FROM gastos WHERE id=?;", (gid,))
    auth.auditar(conn, "Gasto eliminado", f"{g['fecha']} {g['descripcion']} ${money(g['importe_cents'])}")
    conn.commit()
    return ok()


# ---------------------------------------------------------------------------
# Pagos fijos (recordatorios)
# ---------------------------------------------------------------------------
@bp.get("/api/recordatorios")
@auth.requiere("gastos")
def api_recordatorios():
    conn = get_db()
    hoy = _hoy()
    prox = {r["id"]: r for r in fin.recordatorios_proximos(conn, hoy, 31)}
    filas = []
    for r in conn.execute("SELECT * FROM recordatorios ORDER BY activo DESC, dia, servicio;").fetchall():
        x = dict(r)
        p = prox.get(r["id"])
        x.update({"vence": p["vence"] if p else None, "dias_restantes": p["dias_restantes"] if p else None,
                  "vencido": bool(p and p["vencido"]), "periodo": p["periodo"] if p else None})
        filas.append(x)
    return ok(recordatorios=filas, dias_aviso=config.obtener(conn, "recordatorio_dias_aviso"))


def _datos_rec(d: Dict[str, Any]) -> Any:
    servicio = texto(d, "servicio", 80)
    if not servicio:
        return None, "Escribí qué se paga (ej: Alquiler, Internet)."
    dia = int(numero(d, "dia", 1))
    if not 1 <= dia <= 31:
        return None, "El día tiene que estar entre 1 y 31."
    return {"servicio": servicio, "dia": dia, "monto_cents": pesos(d, "monto"),
            "categoria": texto(d, "categoria", 40), "activo": 0 if str(d.get("activo", "1")) in ("0", "false") else 1}, None


@bp.post("/api/recordatorios")
@auth.requiere("gastos")
def api_rec_nuevo():
    r, err = _datos_rec(cuerpo())
    if err:
        return error(err)
    conn = get_db()
    cur = conn.execute("INSERT INTO recordatorios(servicio, dia, monto_cents, categoria, activo) VALUES(?,?,?,?,?);",
                       (r["servicio"], r["dia"], r["monto_cents"], r["categoria"], r["activo"]))
    conn.commit()
    return ok(id=cur.lastrowid)


@bp.put("/api/recordatorios/<int:rid>")
@auth.requiere("gastos")
def api_rec_editar(rid: int):
    r, err = _datos_rec(cuerpo())
    if err:
        return error(err)
    conn = get_db()
    conn.execute("UPDATE recordatorios SET servicio=?, dia=?, monto_cents=?, categoria=?, activo=? WHERE id=?;",
                 (r["servicio"], r["dia"], r["monto_cents"], r["categoria"], r["activo"], rid))
    conn.commit()
    return ok()


@bp.delete("/api/recordatorios/<int:rid>")
@auth.requiere("gastos")
def api_rec_borrar(rid: int):
    conn = get_db()
    conn.execute("DELETE FROM recordatorios WHERE id=?;", (rid,))
    conn.commit()
    return ok()


@bp.post("/api/recordatorios/<int:rid>/pagar")
@auth.requiere("gastos")
def api_rec_pagar(rid: int):
    """Marca el pago del período y, si se pide, lo registra como gasto."""
    d = cuerpo()
    conn = get_db()
    r = conn.execute("SELECT * FROM recordatorios WHERE id=?;", (rid,)).fetchone()
    if r is None:
        return error("Ese pago fijo ya no existe.", 404)
    hoy = _hoy()
    per = texto(d, "periodo", 7) or hoy[:7]
    gid = None
    if str(d.get("registrar_gasto", "1")) not in ("0", "false"):
        importe = pesos(d, "importe", int(r["monto_cents"] or 0))
        if importe <= 0:
            return error("Indicá cuánto se pagó para registrarlo como gasto.")
        gid = _insertar_gasto(conn, {"fecha": fecha(d.get("fecha"), hoy), "categoria": r["categoria"] or "",
                                     "descripcion": f"{r['servicio']} ({per})", "proveedor": "",
                                     "importe_cents": importe, "metodo_pago": texto(d, "metodo_pago", 40),
                                     "observaciones": "Pago fijo"})
    conn.execute("UPDATE recordatorios SET ultimo_pago=? WHERE id=?;", (per, rid))
    conn.commit()
    return ok(gasto_id=gid)


# ---------------------------------------------------------------------------
# Reportes y exportación
# ---------------------------------------------------------------------------
@bp.get("/api/reportes")
@auth.requiere("reportes")
def api_reportes():
    conn = get_db()
    desde, hasta = periodo()
    hoy = _hoy()
    ganancias = {}
    for k, rg in _rangos(hoy).items():
        ganancias[k] = fin.resumen(conn, rg["desde"], rg["hasta"], detalle=False)["balance_cents"]
    return ok(resumen=fin.resumen(conn, desde, hasta), ganancias=ganancias,
              meses=fin.comparacion_meses(conn, hoy, 6),
              ganancia_productos=costos.ganancia_por_producto(conn, desde, hasta))


# ---------------------------------------------------------------------------
# Mejores clientes
# ---------------------------------------------------------------------------
def _mejores_args() -> Any:
    opciones = clientes.periodos(_hoy())
    pid = str(request.args.get("periodo") or opciones[0]["id"])
    elegido = next((o for o in opciones if o["id"] == pid), None)
    orden = request.args.get("orden") or "pedidos"
    return opciones, elegido, (orden if orden in clientes.ORDENES else "pedidos")


@bp.get("/api/clientes/mejores")
@auth.requiere("reportes")
def api_mejores_clientes():
    opciones, elegido, orden = _mejores_args()
    if elegido is None:
        return error(f"Elegí uno de los últimos {clientes.MESES_HACIA_ATRAS} meses.")
    cantidad = max(1, min(int(numero(request.args, "cantidad", 5)), 50))
    datos = clientes.mejores(get_db(), elegido["desde"], elegido["hasta"], cantidad, orden)
    return ok(periodos=opciones, periodo=elegido, orden=orden, **datos)


@bp.get("/api/clientes/mejores.xlsx")
@auth.requiere("reportes")
def api_mejores_clientes_xlsx():
    """Todos los clientes del período, en el mismo orden que la pantalla."""
    _, elegido, orden = _mejores_args()
    if elegido is None:
        return error(f"Elegí uno de los últimos {clientes.MESES_HACIA_ATRAS} meses.")
    conn = get_db()
    datos = clientes.mejores(conn, elegido["desde"], elegido["hasta"], 0, orden)
    hoja = {
        "nombre": "Clientes",
        "columnas": [("Puesto", "numero"), ("Dirección", "texto"), ("Teléfono", "texto"), ("Pedidos", "numero"),
                     ("Comprado (sin envío)", "moneda"), ("Ticket promedio", "moneda"), ("Lo que más pide", "texto"),
                     ("Primer pedido", "texto"), ("Último pedido", "texto")],
        "filas": [[c["puesto"], c["direccion"], " / ".join(c["telefonos"]), c["pedidos"], c["total_cents"],
                   c["ticket_promedio_cents"], (c["favorito"] or {}).get("nombre", ""),
                   str(c["primero"])[:10], str(c["ultimo"])[:10]] for c in datos["clientes"]],
        "total": ["", "TOTAL", "", datos["pedidos_total"], datos["ventas_total_cents"], "", "", "", ""],
    }
    contenido = xlsx([hoja], config.obtener(conn, "moneda_simbolo"))
    return send_file(io.BytesIO(contenido), as_attachment=True,
                     download_name=f"mejores_clientes_{elegido['id']}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _tipo() -> str:
    t = (request.args.get("tipo") or "completo").strip()
    return t if t in ("ventas", "gastos", "balance", "completo") else "completo"


def _nombre(ext: str) -> str:
    desde, hasta = periodo()
    rango = "historico" if desde == "0000-01-01" and hasta == "9999-12-31" else f"{desde}_a_{hasta}".replace(
        "0000-01-01", "inicio").replace("9999-12-31", "hoy")
    return f"reporte_{_tipo()}_{rango}.{ext}"


@bp.get("/api/exportar.xlsx")
@auth.requiere("reportes", "gastos", "ventas")
def api_exportar_xlsx():
    conn = get_db()
    desde, hasta = periodo()
    tipo = _tipo()
    p = auth.usuario_actual()["permisos"]
    puede = "reportes" in p or (tipo == "gastos" and "gastos" in p) or (tipo == "ventas" and "ventas" in p)
    if not puede:
        return error("Tu usuario no tiene permiso para exportar ese reporte.", 403)
    datos = xlsx(hojas_reporte(conn, tipo, desde, hasta), config.obtener(conn, "moneda_simbolo"))
    return send_file(io.BytesIO(datos), as_attachment=True, download_name=_nombre("xlsx"),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.get("/api/exportar.pdf")
@auth.requiere("reportes")
def api_exportar_pdf():
    conn = get_db()
    desde, hasta = periodo()
    try:
        datos = pdf_reporte(conn, _tipo(), desde, hasta)
    except Exception as e:
        return error(f"No se pudo generar el PDF: {e}", 500)
    return send_file(io.BytesIO(datos), as_attachment=True, download_name=_nombre("pdf"), mimetype="application/pdf")
