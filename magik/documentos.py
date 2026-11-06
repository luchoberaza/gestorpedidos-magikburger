"""PDF del cierre, PDF de reportes y Excel (.xlsx) sin dependencias externas."""
from __future__ import annotations

import importlib
import io
import os
import subprocess
import sys
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence
from xml.sax.saxutils import escape

from . import config
from .db import APP_DIR, carpeta, carpeta_datos
from .dominio import finanzas, liquidacion
from .dominio.pedidos import money


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def asegurar_reportlab() -> None:
    try:
        importlib.import_module("reportlab")
        return
    except ModuleNotFoundError:
        pass
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "reportlab"])
    except Exception as e:
        raise RuntimeError(
            "Falta la librería para generar PDF (reportlab) y no se pudo instalar sola. "
            f"Conectá la PC a internet y volvé a intentar. Detalle: {e}")
    importlib.import_module("reportlab")


def _fuentes() -> Dict[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    nombres = {"reg": "Poppins", "sb": "Poppins-SB", "b": "Poppins-B"}
    archivos = {"reg": "Poppins-Regular.ttf", "sb": "Poppins-SemiBold.ttf", "b": "Poppins-Bold.ttf"}
    registradas = set(pdfmetrics.getRegisteredFontNames())
    try:
        for k, nombre in nombres.items():
            if nombre in registradas:
                continue
            for base in (os.path.join(APP_DIR, "static"), APP_DIR):
                p = os.path.join(base, archivos[k])
                if os.path.exists(p):
                    pdfmetrics.registerFont(TTFont(nombre, p))
                    break
            else:
                raise FileNotFoundError(archivos[k])
        return nombres
    except Exception:
        return {"reg": "Helvetica", "sb": "Helvetica-Bold", "b": "Helvetica-Bold"}


def _ruta_logo() -> Optional[str]:
    from .ticket import ruta_logo_propio

    p = ruta_logo_propio()
    if p:
        return p
    p = os.path.join(APP_DIR, "static", "logo.png")
    return p if os.path.exists(p) else None


def _esc(s: Any) -> str:
    return escape(str(s if s is not None else ""))


def guardar_reporte(nombre: str, datos: bytes) -> Optional[str]:
    """Archiva el PDF en <datos>/reportes sin pisar uno anterior. None si no se pudo."""
    try:
        d = carpeta("reportes")
        destino = os.path.join(d, nombre)
        base, ext = os.path.splitext(nombre)
        n = 2
        while os.path.exists(destino):
            destino = os.path.join(d, f"{base}_{n}{ext}")
            n += 1
        with open(destino, "wb") as f:
            f.write(datos)
        return os.path.basename(destino)
    except OSError as e:
        print(f"[magik] NO se pudo archivar el reporte: {e}")
        return None


def ruta_reporte(nombre: str) -> Optional[str]:
    p = os.path.join(carpeta_datos(), "reportes", os.path.basename(nombre or ""))
    return p if nombre and os.path.exists(p) else None


# ---------------------------------------------------------------------------
# PDF del cierre de jornada (diseño oscuro de siempre)
# ---------------------------------------------------------------------------
def pdf_cierre(conn: Any, jornada: str, incluir_ajustes: bool = True,
               cierre_id: Optional[int] = None) -> bytes:
    """Detalle de los pedidos abiertos (o de un cierre ya hecho) + efectivo a rendir."""
    asegurar_reportlab()
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    F = _fuentes()
    cfg = config.todas(conn)
    sim = cfg["moneda_simbolo"]
    filtro = "o.cierre_id = ?" if cierre_id is not None else "o.cierre_id IS NULL"
    params = [cierre_id] if cierre_id is not None else []
    pedidos = conn.execute(
        f"SELECT o.*, c.name AS courier_name FROM orders o LEFT JOIN couriers c ON c.id=o.courier_id "
        f"WHERE {filtro} ORDER BY o.id;", params).fetchall()
    ids = [int(o["id"]) for o in pedidos]
    items_por: Dict[int, List[Any]] = {}
    mods_por: Dict[int, List[Any]] = {}
    ajustes_por: Dict[int, List[Any]] = {}
    if ids:
        q = ",".join("?" * len(ids))
        for it in conn.execute(f"SELECT * FROM order_items WHERE order_id IN ({q}) ORDER BY order_id, id;", ids):
            items_por.setdefault(int(it["order_id"]), []).append(it)
        for m in conn.execute(f"SELECT m.* FROM order_item_mods m JOIN order_items i ON i.id=m.order_item_id "
                              f"WHERE i.order_id IN ({q}) ORDER BY m.id;", ids):
            mods_por.setdefault(int(m["order_item_id"]), []).append(m)
        for a in conn.execute(f"SELECT * FROM order_adjustments WHERE order_id IN ({q}) "
                              f"ORDER BY sort_order, id;", ids):
            ajustes_por.setdefault(int(a["order_id"]), []).append(a)

    if cierre_id is not None:
        liq = liquidacion.calcular(conn, cierre_id=cierre_id)
        carry = {"count": 0, "total_cents": 0}
    else:
        liq = liquidacion.calcular(conn, jornada)
        carry = liquidacion.arrastrados(conn, jornada)
    medios = liquidacion.medios_de_pago(conn)

    BG, CARD, GLASS = colors.HexColor("#0B0D15"), colors.HexColor("#161A2B"), colors.HexColor("#1E223A")
    BORDER, ACC = colors.HexColor("#2B3052"), colors.HexColor("#7EA6FF")
    TEXT, MUTED = colors.HexColor("#E5E7EB"), colors.HexColor("#9CA3AF")
    s_title = ParagraphStyle("t", fontName=F["b"], fontSize=19, leading=23, textColor=TEXT)
    s_sub = ParagraphStyle("s", fontName=F["reg"], fontSize=10, leading=14, textColor=MUTED)
    s_h = ParagraphStyle("h", fontName=F["sb"], fontSize=12.5, leading=16, textColor=TEXT, spaceBefore=10, spaceAfter=6)
    s_txt = ParagraphStyle("x", fontName=F["reg"], fontSize=9.5, leading=14, textColor=TEXT)
    s_mut = ParagraphStyle("m", fontName=F["reg"], fontSize=9, leading=13, textColor=MUTED)
    s_kpi = ParagraphStyle("k", fontName=F["b"], fontSize=11.5, leading=14, textColor=ACC)
    logo = None
    ruta = _ruta_logo()
    if ruta:
        try:
            logo = ImageReader(ruta)
        except Exception:
            logo = None

    def fondo(canvas: Any, _doc: Any) -> None:
        w, h = A4
        canvas.saveState()
        canvas.setFillColor(BG)
        canvas.rect(0, 0, w, h, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#14204A"))
        canvas.circle(w * 0.88, h * 0.82, 120, fill=1, stroke=0)
        if logo is not None:
            try:
                canvas.drawImage(logo, 16 * mm, h - 22 * mm, 12 * mm, 12 * mm, mask="auto", preserveAspectRatio=True)
            except Exception:
                pass
        canvas.restoreState()

    def caja(filas: List[Any], fondo_color: Any = GLASS) -> Any:
        t = Table(filas, colWidths=[None])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), fondo_color), ("BOX", (0, 0), (-1, -1), 1, BORDER),
                               ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                               ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
        return t

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=26 * mm,
                            bottomMargin=16 * mm, title=f"Cierre {jornada}")
    story: List[Any] = []
    cab = Table([[Paragraph(f"{_esc(cfg['negocio_nombre'])} — Cierre de jornada", s_title),
                  Paragraph(_esc(jornada), s_sub)]], colWidths=[None, 40 * mm])
    cab.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CARD), ("BOX", (0, 0), (-1, -1), 1, BORDER),
                             ("LEFTPADDING", (0, 0), (-1, -1), 16), ("TOPPADDING", (0, 0), (-1, -1), 14),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 14), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [cab, Spacer(1, 10), Paragraph("Pedidos de la jornada", s_h)]

    if not pedidos:
        story.append(caja([[Paragraph("No hubo pedidos.", s_txt)]]))
    for o in pedidos:
        oid = int(o["id"])
        creado = str(o["created_at"]).replace("T", " ")
        filas_items = []
        for it in items_por.get(oid, []):
            txt = []
            for m in mods_por.get(int(it["id"]), []):
                if m["kind"] == "remove":
                    txt.append(f"sin {_esc(m['name_snapshot'])}")
                else:
                    p = int(m["price_cents"] or 0)
                    txt.append(f"+{_esc(m['name_snapshot'])}" + (f" (+{sim}{money(p)})" if p else ""))
            nombre = _esc(it["product_name_snapshot"] or it["name_snapshot"])
            if it["promo_group"]:
                nombre = f"{nombre} <font color='#9CA3AF'>· promo {_esc(it['promo_name_snapshot'] or '')}</font>"
            filas_items.append([Paragraph(f"<b>{int(it['qty'] or 1)}×</b>", s_txt), Paragraph(nombre, s_txt),
                                Paragraph("<br/>".join(txt) or "<font color='#9CA3AF'>—</font>", s_mut)])
        if not filas_items:
            filas_items = [[Paragraph("—", s_mut), Paragraph("Sin ítems", s_mut), Paragraph("—", s_mut)]]
        ti = Table(filas_items, colWidths=[12 * mm, 78 * mm, None], hAlign="LEFT")
        ti.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                                ("LINEBELOW", (0, 0), (-1, -1), 0.25, BORDER)]))
        estado = liquidacion.ESTADOS_ETIQUETA.get(o["status"], o["status"])
        filas = [
            [Paragraph(f"<b>#{oid}</b> · {creado[8:10]}/{creado[5:7]} {creado[11:16]} · "
                       f"<b>{_esc(o['courier_name'] or 'Sin repartidor')}</b> · {_esc(estado)}", s_txt)],
            [Paragraph(f"<font color='#9CA3AF'>Pago:</font> <b>{_esc(liquidacion.nombre_medio(medios, o['payment_method']))}</b>"
                       f" · <font color='#9CA3AF'>Total:</font> <b>{sim}{money(o['total_cents'])}</b>", s_mut)],
            [Paragraph(f"<font color='#9CA3AF'>Tel:</font> {_esc(o['phone'])} · "
                       f"<font color='#9CA3AF'>Dir:</font> {_esc(o['address'])}", s_mut)],
            [Spacer(1, 2 * mm)], [ti],
        ]
        if (o["notes"] if "notes" in o.keys() else ""):
            filas.append([Paragraph(f"<font color='#9CA3AF'>Nota:</font> {_esc(o['notes'])}", s_mut)])
        if incluir_ajustes and ajustes_por.get(oid):
            partes = [f"{_esc(a['label'])}: <b>{'+' if int(a['amount_cents']) >= 0 else '−'}{sim}"
                      f"{money(abs(int(a['amount_cents'])))}</b>" for a in ajustes_por[oid]]
            filas.append([Paragraph("<font color='#9CA3AF'>Ajustes:</font> " + " · ".join(partes), s_mut)])
        story.append(KeepTogether([caja(filas), Spacer(1, 8)]))

    story += [Spacer(1, 6), Paragraph("Efectivo a rendir por repartidor", s_h), Paragraph(
        "Solo pedidos ENTREGADOS. Efectivo cobrado − envíos (quedan para el repartidor) + el cambio que "
        "devuelve. Lo cobrado por otros medios se muestra para que la cuenta se entienda, pero NO se rinde.",
        s_mut), Spacer(1, 6)]
    if not liq["couriers"]:
        story.append(Paragraph("No hubo pedidos.", s_txt))
    else:
        filas = [[Paragraph(f"<b>{t}</b>", s_txt) for t in
                  ("Repartidor", "Efectivo", "Envíos", "Cambio", "A rendir", "Otros medios")]]
        for d in liq["couriers"]:
            filas.append([Paragraph(_esc(d["courier_name"]), s_txt), Paragraph(f"{sim}{money(d['cash_cents'])}", s_txt),
                          Paragraph(f"−{sim}{money(d['delivery_cents'])}", s_mut),
                          Paragraph(f"+{sim}{money(d['cash_float_cents'])}", s_mut),
                          Paragraph(f"<b>{sim}{money(d['to_render_cents'])}</b>", s_kpi),
                          Paragraph(f"{sim}{money(d['transfer_cents'])}", s_mut)])
        filas.append([Paragraph("<b>TOTAL</b>", s_txt), Paragraph("", s_txt), Paragraph("", s_txt),
                      Paragraph("", s_txt), Paragraph(f"<b>{sim}{money(liq['cash_to_render_cents'])}</b>", s_kpi),
                      Paragraph(f"{sim}{money(liq['transfer_cents'])}", s_mut)])
        t = Table(filas, colWidths=[None, 24 * mm, 22 * mm, 22 * mm, 28 * mm, 26 * mm], hAlign="LEFT")
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), CARD), ("BOX", (0, 0), (-1, -1), 1, BORDER),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [GLASS, colors.HexColor("#202544")]),
                               ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 9),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))
        story.append(t)
    if liq["pending_count"]:
        story += [Spacer(1, 8), Paragraph(
            f"<b>Pendientes de entrega:</b> {liq['pending_count']} pedido(s) por {sim}{money(liq['pending_cents'])}. "
            f"No entran en el efectivo a rendir porque todavía no se cobraron.", s_mut)]
    if carry["count"]:
        story += [Spacer(1, 10), Paragraph("Pedidos de jornadas anteriores", s_h), Paragraph(
            f"Quedaron {carry['count']} pedido(s) abiertos de jornadas previas, por {sim}{money(carry['total_cents'])}. "
            f"Están en el detalle y se cierran ahora, pero NO se suman al efectivo a rendir de hoy.", s_mut)]
    story += [Spacer(1, 12), Paragraph(f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}", s_mut)]
    doc.build(story, onFirstPage=fondo, onLaterPages=fondo)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF de reportes (claro, para imprimir o compartir)
# ---------------------------------------------------------------------------
def pdf_reporte(conn: Any, tipo: str, desde: str, hasta: str) -> bytes:
    asegurar_reportlab()
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    F = _fuentes()
    cfg = config.todas(conn)
    sim = cfg["moneda_simbolo"]
    r = finanzas.resumen(conn, desde, hasta)
    INK, MUT, ACC, LINE = (colors.HexColor("#111827"), colors.HexColor("#6B7280"),
                           colors.HexColor("#2F6BEF"), colors.HexColor("#E5E7EB"))
    s_t = ParagraphStyle("t", fontName=F["b"], fontSize=18, leading=22, textColor=INK)
    s_s = ParagraphStyle("s", fontName=F["reg"], fontSize=9.5, leading=13, textColor=MUT)
    s_h = ParagraphStyle("h", fontName=F["sb"], fontSize=12, leading=15, textColor=INK, spaceBefore=12, spaceAfter=6)
    s_c = ParagraphStyle("c", fontName=F["reg"], fontSize=8.5, leading=11, textColor=INK)
    s_cb = ParagraphStyle("cb", fontName=F["sb"], fontSize=8.5, leading=11, textColor=INK)

    def tabla(cabecera: Sequence[str], filas: List[Sequence[Any]], anchos: List[Any], total: Optional[Sequence[Any]] = None) -> Any:
        datos = [[Paragraph(_esc(c), s_cb) for c in cabecera]]
        datos += [[Paragraph(_esc(c), s_c) for c in f] for f in filas]
        if total:
            datos.append([Paragraph(_esc(c), s_cb) for c in total])
        if len(datos) == 1:
            datos.append([Paragraph("Sin datos en el período", s_c)] + [""] * (len(cabecera) - 1))
        t = Table(datos, colWidths=anchos, repeatRows=1, hAlign="LEFT")
        estilo = [("LINEBELOW", (0, 0), (-1, 0), 1, ACC), ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
                  ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                  ("LEFTPADDING", (0, 0), (-1, -1), 4), ("VALIGN", (0, 0), (-1, -1), "TOP")]
        if total:
            estilo.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F3F6FD")))
        t.setStyle(TableStyle(estilo))
        return t

    def m(c: Any) -> str:
        return f"{sim}{money(c):,}".replace(",", ".")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                            bottomMargin=14 * mm, title=f"Reporte {desde} a {hasta}")
    nombres = {"ventas": "Ventas", "gastos": "Gastos", "balance": "Balance", "completo": "Reporte completo"}
    story: List[Any] = [Paragraph(f"{_esc(cfg['negocio_nombre'])} — {nombres.get(tipo, 'Reporte')}", s_t),
                        Paragraph(f"Período: {_fecha(desde)} al {_fecha(hasta)} · Generado: "
                                  f"{datetime.now().strftime('%d/%m/%Y %H:%M')}", s_s), Spacer(1, 8)]
    kpis = [("Ventas", m(r["ventas_cents"])), ("Gastos", m(r["gastos_cents"])), ("Balance", m(r["balance_cents"])),
            ("Pedidos", str(r["pedidos_count"])), ("Ticket promedio", m(r["ticket_promedio_cents"])),
            ("Envíos (repartidores)", m(r["envios_cents"]))]
    t = Table([[Paragraph(f"<font color='#6B7280' size=8>{k}</font><br/><b>{v}</b>", s_c) for k, v in kpis[:3]],
               [Paragraph(f"<font color='#6B7280' size=8>{k}</font><br/><b>{v}</b>", s_c) for k, v in kpis[3:]]],
              colWidths=[None, None, None])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    story.append(t)

    if tipo in ("ventas", "balance", "completo"):
        dias = [d for d in r["serie_dias"] if d["ventas"] or d["gastos"] or d["pedidos"]]
        story += [Paragraph("Por día", s_h), tabla(["Día", "Pedidos", "Ventas", "Gastos", "Balance"],
                  [[_fecha(d["dia"]), d["pedidos"], m(d["ventas"]), m(d["gastos"]), m(d["ventas"] - d["gastos"])]
                   for d in dias], [32 * mm, 22 * mm, None, None, None],
                  ["Total", r["pedidos_count"], m(r["ventas_cents"]), m(r["gastos_cents"]), m(r["balance_cents"])])]
    if tipo in ("ventas", "completo"):
        story += [Paragraph("Productos más vendidos", s_h),
                  tabla(["#", "Producto", "Unidades", "Total"],
                        [[i + 1, p["nombre"], p["unidades"], m(p["total_cents"])]
                         for i, p in enumerate(finanzas.ranking_productos(conn, desde, hasta, 25))],
                        [10 * mm, None, 24 * mm, 32 * mm]),
                  Paragraph("Ventas por medio de pago", s_h),
                  tabla(["Medio", "Total"], [[x["medio"], m(x["total_cents"])] for x in r["ventas_por_medio"]],
                        [None, 40 * mm])]
        manuales = finanzas.ventas_manuales(conn, desde, hasta)
        if manuales:
            story += [Paragraph("Ventas manuales", s_h), tabla(
                ["Fecha", "Concepto", "Cant.", "Total", "Pago"],
                [[_fecha(v["fecha"]), v["concepto"], _num(v["cantidad"]), m(v["total_cents"]), v["metodo_pago"]]
                 for v in manuales], [22 * mm, None, 16 * mm, 28 * mm, 30 * mm])]
    if tipo in ("gastos", "balance", "completo"):
        story += [Paragraph("Gastos por categoría", s_h), tabla(
            ["Categoría", "Total"], [[x["categoria"], m(x["total_cents"])] for x in r["gastos_por_categoria"]],
            [None, 40 * mm], ["Total", m(r["gastos_cents"])])]
    if tipo in ("gastos", "completo"):
        story += [Paragraph("Detalle de gastos", s_h), tabla(
            ["Fecha", "Categoría", "Descripción", "Proveedor", "Importe", "Pago"],
            [[_fecha(g["fecha"]), g["categoria"], g["descripcion"], g["proveedor"], m(g["importe_cents"]),
              g["metodo_pago"]] for g in finanzas.gastos(conn, desde, hasta)],
            [20 * mm, 26 * mm, None, 28 * mm, 24 * mm, 24 * mm], ["", "", "", "Total", m(r["gastos_cents"]), ""])]
    doc.build(story)
    return buf.getvalue()


def _fecha(iso: Any) -> str:
    s = str(iso or "")
    return f"{s[8:10]}/{s[5:7]}/{s[0:4]}" if len(s) >= 10 else s


def _num(x: Any) -> Any:
    try:
        f = float(x)
        return int(f) if f.is_integer() else round(f, 2)
    except (TypeError, ValueError):
        return x


# ---------------------------------------------------------------------------
# Excel (.xlsx) mínimo, escrito a mano (zip + XML): no requiere librerías
# ---------------------------------------------------------------------------
def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def xlsx(hojas: List[Dict[str, Any]], simbolo: str = "$") -> bytes:
    """hojas: [{nombre, columnas:[(titulo, tipo)], filas:[[...]], total: [...]?}]
    tipo: 'texto' | 'numero' | 'moneda' (centavos → pesos)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(len(hojas))) + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   '</Relationships>')
        nombres_usados = set()
        hojas_xml = []
        for i, h in enumerate(hojas):
            nombre = "".join(c for c in str(h["nombre"]) if c not in '[]:*?/\\')[:31] or f"Hoja{i + 1}"
            while nombre in nombres_usados:
                nombre = nombre[:28] + f"_{i}"
            nombres_usados.add(nombre)
            hojas_xml.append(f'<sheet name="{escape(nombre)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   f'<sheets>{"".join(hojas_xml)}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                             for i in range(len(hojas)))
                   + f'<Relationship Id="rId{len(hojas) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   '</Relationships>')
        # Estilos: 0 normal · 1 encabezado · 2 moneda · 3 total texto · 4 total moneda · 5 número
        z.writestr("xl/styles.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   f'<numFmts count="1"><numFmt numFmtId="164" formatCode="&quot;{escape(simbolo)}&quot; #,##0"/></numFmts>'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                   '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
                   '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
                   '<fill><patternFill patternType="solid"><fgColor rgb="FF2F6BEF"/></patternFill></fill>'
                   '<fill><patternFill patternType="solid"><fgColor rgb="FFE8EEFD"/></patternFill></fill></fills>'
                   '<borders count="1"><border/></borders>'
                   '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                   '<cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                   '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
                   '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
                   '<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>'
                   '<xf numFmtId="164" fontId="0" fillId="3" borderId="0" xfId="0" applyNumberFormat="1" applyFill="1"/>'
                   '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
                   '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
                   '</styleSheet>')
        for i, h in enumerate(hojas):
            cols = h["columnas"]
            filas_xml = []

            def celda(ref: str, valor: Any, tipo: str, total: bool = False) -> str:
                st = ' s="3"' if total else ""
                if valor is None or valor == "":
                    return f'<c r="{ref}"{st}/>'
                if tipo in ("moneda", "numero") and isinstance(valor, (int, float)) and not isinstance(valor, bool):
                    v = valor / 100 if tipo == "moneda" else valor
                    estilo = (4 if total else 2) if tipo == "moneda" else (3 if total else 5)
                    return f'<c r="{ref}" s="{estilo}"><v>{v}</v></c>'
                return (f'<c r="{ref}" t="inlineStr"{st}>'
                        f'<is><t xml:space="preserve">{escape(str(valor))}</t></is></c>')

            filas_xml.append('<row r="1">' + "".join(
                f'<c r="{_col(j)}1" t="inlineStr" s="1"><is><t>{escape(t)}</t></is></c>'
                for j, (t, _) in enumerate(cols)) + "</row>")
            n = 1
            for fila in h["filas"]:
                n += 1
                filas_xml.append(f'<row r="{n}">' + "".join(
                    celda(f"{_col(j)}{n}", v, cols[j][1]) for j, v in enumerate(fila)) + "</row>")
            if not h["filas"]:
                n += 1
                filas_xml.append(f'<row r="{n}">' + celda(f"A{n}", "Sin datos en el período", "texto") + "</row>")
            if h.get("total"):
                n += 1
                filas_xml.append(f'<row r="{n}">' + "".join(
                    celda(f"{_col(j)}{n}", v, cols[j][1], True) for j, v in enumerate(h["total"])) + "</row>")
            anchos = []
            for j, (t, tipo) in enumerate(cols):
                largo = max([len(t)] + [len(str(f[j])) for f in h["filas"][:500] if j < len(f)])
                anchos.append(f'<col min="{j + 1}" max="{j + 1}" width="{min(60, max(10, largo + 3))}" customWidth="1"/>')
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                       f'<cols>{"".join(anchos)}</cols><sheetData>{"".join(filas_xml)}</sheetData></worksheet>')
    return buf.getvalue()


def hojas_reporte(conn: Any, tipo: str, desde: str, hasta: str) -> List[Dict[str, Any]]:
    r = finanzas.resumen(conn, desde, hasta)
    hojas: List[Dict[str, Any]] = []
    if tipo in ("balance", "completo"):
        hojas.append({"nombre": "Resumen", "columnas": [("Concepto", "texto"), ("Valor", "moneda")], "filas": [
            ["Período", f"{_fecha(desde)} al {_fecha(hasta)}"],
            ["Ventas (pedidos, sin envíos)", r["ventas_pedidos_cents"]],
            ["Ventas manuales", r["ventas_manuales_cents"]],
            ["Total vendido", r["ventas_cents"]],
            ["Total gastado", r["gastos_cents"]],
            ["Balance", r["balance_cents"]],
            ["Envíos (de los repartidores)", r["envios_cents"]],
            ["Ticket promedio", r["ticket_promedio_cents"]],
        ]})
        hojas.append({"nombre": "Por día", "columnas": [("Día", "texto"), ("Pedidos", "numero"), ("Ventas", "moneda"),
                                                        ("Gastos", "moneda"), ("Balance", "moneda")],
                      "filas": [[_fecha(d["dia"]), d["pedidos"], d["ventas"], d["gastos"], d["ventas"] - d["gastos"]]
                                for d in r["serie_dias"] if d["ventas"] or d["gastos"] or d["pedidos"]],
                      "total": ["TOTAL", r["pedidos_count"], r["ventas_cents"], r["gastos_cents"], r["balance_cents"]]})
    if tipo in ("ventas", "completo"):
        ped = finanzas.pedidos_del_periodo(conn, desde, hasta)
        hojas.append({"nombre": "Pedidos", "columnas": [
            ("N°", "numero"), ("Jornada", "texto"), ("Hora", "texto"), ("Dirección", "texto"), ("Teléfono", "texto"),
            ("Repartidor", "texto"), ("Pago", "texto"), ("Estado", "texto"), ("Total", "moneda"),
            ("Envío", "moneda"), ("Venta neta", "moneda")],
            "filas": [[p["id"], _fecha(p["jornada"]), str(p["created_at"]).replace("T", " ")[11:16], p["address"],
                       p["phone"], p["courier_name"], p["medio"], p["status_label"], p["total_cents"],
                       p["envio_cents"], p["neto_cents"]] for p in ped],
            "total": ["TOTAL", "", "", "", "", "", "", "", sum(p["total_cents"] for p in ped),
                      sum(p["envio_cents"] for p in ped), sum(p["neto_cents"] for p in ped)]})
        man = finanzas.ventas_manuales(conn, desde, hasta)
        hojas.append({"nombre": "Ventas manuales", "columnas": [
            ("Fecha", "texto"), ("Hora", "texto"), ("Concepto", "texto"), ("Cantidad", "numero"),
            ("Precio unitario", "moneda"), ("Total", "moneda"), ("Pago", "texto"), ("Observaciones", "texto")],
            "filas": [[_fecha(v["fecha"]), v["hora"], v["concepto"], _num(v["cantidad"]), v["precio_unit_cents"],
                       v["total_cents"], v["metodo_pago"], v["observaciones"]] for v in man],
            "total": ["TOTAL", "", "", "", "", sum(int(v["total_cents"]) for v in man), "", ""]})
        hojas.append({"nombre": "Productos", "columnas": [("Producto", "texto"), ("Unidades", "numero"),
                                                          ("Total", "moneda")],
                      "filas": [[p["nombre"], p["unidades"], p["total_cents"]]
                                for p in finanzas.ranking_productos(conn, desde, hasta, 0)]})
    if tipo in ("gastos", "completo"):
        gs = finanzas.gastos(conn, desde, hasta)
        hojas.append({"nombre": "Gastos", "columnas": [
            ("Fecha", "texto"), ("Categoría", "texto"), ("Descripción", "texto"), ("Proveedor", "texto"),
            ("Importe", "moneda"), ("Pago", "texto"), ("Observaciones", "texto")],
            "filas": [[_fecha(g["fecha"]), g["categoria"], g["descripcion"], g["proveedor"], g["importe_cents"],
                       g["metodo_pago"], g["observaciones"]] for g in gs],
            "total": ["TOTAL", "", "", "", sum(int(g["importe_cents"]) for g in gs), "", ""]})
    return hojas
