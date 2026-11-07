"""Ticket del pedido: datos, vista HTML, ESC/POS directo e impresoras de Windows.

La vista HTML y la impresión térmica salen del MISMO contexto, así que nunca
pueden mostrar cosas distintas. Textos, logo y copias vienen de Configuración.
"""
from __future__ import annotations

import base64
import io
import os
import sqlite3
import sys
import unicodedata
from typing import Any, Dict, List, Optional

from . import config
from .db import carpeta_datos
from .dominio.liquidacion import medios_de_pago
from .recursos import LOGO_ESCPOS_B64, POPPINS_BOLD_B64, POPPINS_REG_B64

ANCHO = 48  # columnas de una térmica de 80 mm en fuente A
ESC, GS = b"\x1b", b"\x1d"
ANCHO_PX = 576


# ---------------------------------------------------------------------------
# Contexto
# ---------------------------------------------------------------------------
def contexto(conn: sqlite3.Connection, order_id: int, copias: Optional[int] = None) -> Optional[Dict[str, Any]]:
    o = conn.execute(
        "SELECT o.*, c.name AS courier_name FROM orders o LEFT JOIN couriers c ON c.id=o.courier_id "
        "WHERE o.id=?;", (order_id,)).fetchone()
    if not o:
        return None
    items = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id;", (order_id,)).fetchall()
    mods = conn.execute(
        "SELECT * FROM order_item_mods WHERE order_item_id IN (SELECT id FROM order_items WHERE order_id=?) "
        "ORDER BY id;", (order_id,)).fetchall()
    ajustes = [{"label": r["label"], "amount_cents": int(r["amount_cents"] or 0)} for r in conn.execute(
        "SELECT label, amount_cents FROM order_adjustments WHERE order_id=? ORDER BY sort_order, id;",
        (order_id,)).fetchall()]

    por_item: Dict[int, List[Any]] = {}
    for m in mods:
        por_item.setdefault(int(m["order_item_id"]), []).append(m)

    bloques: List[Dict[str, Any]] = []
    promo_por_grupo: Dict[str, Dict[str, Any]] = {}
    for it in items:
        iid, qty = int(it["id"]), int(it["qty"] or 1)
        base = int(it["product_price_cents_snapshot"] or 0) or int(it["base_price_cents"] or 0)
        quitados, agregados, extras = [], {}, 0
        for m in por_item.get(iid, []):
            if m["kind"] == "remove":
                quitados.append(m["name_snapshot"])
            elif m["kind"] == "add":
                precio = int(m["price_cents"] or 0)
                extras += precio
                a = agregados.setdefault(int(m["ingredient_id"] or 0),
                                         {"name": m["name_snapshot"], "count": 0, "price_cents_total": 0})
                a["count"] += 1
                a["price_cents_total"] += precio
        item = {
            "name": it["product_name_snapshot"] or it["name_snapshot"] or "Producto",
            "qty": qty, "base_price_cents": base, "line_total_cents": (base + extras) * qty,
            "removed": quitados, "added": list(agregados.values()),
        }
        grupo = it["promo_group"]
        if grupo:
            b = promo_por_grupo.get(grupo)
            if b is None:
                b = promo_por_grupo[grupo] = {"kind": "promo", "promo_name": it["promo_name_snapshot"] or "Promo",
                                              "promo_price_cents": 0, "extras_cents": 0, "components": []}
                bloques.append(b)
            b["components"].append(item)
            b["extras_cents"] += extras * qty
            if int(it["promo_price_cents"] or 0):
                b["promo_price_cents"] = int(it["promo_price_cents"])
        else:
            item["kind"] = "item"
            item["unidades"] = unidades(qty)
            bloques.append(item)
    for b in bloques:
        if b.get("kind") == "promo":
            b["line_total_cents"] = b["promo_price_cents"] + b["extras_cents"]

    medios = medios_de_pago(conn)
    medio = medios.get(o["payment_method"] or "cash")
    if medio is None:
        es_transf = str(o["payment_method"] or "").startswith("transf")
        medio = {"name": "Transferencia" if es_transf else "Efectivo", "hide_prices_ticket": 1 if es_transf else 0,
                 "code": o["payment_method"]}
    ocultar = bool(medio["hide_prices_ticket"])
    cfg = config.todas(conn)
    if copias is None or copias <= 0:
        copias = int(cfg["ticket_copies"])

    creado = str(o["created_at"]).replace("T", " ")
    return {
        "order": {
            "id": int(o["id"]), "ddmm": f"{creado[8:10]}/{creado[5:7]}", "hhmm": creado[11:16],
            "phone": o["phone"], "address": o["address"], "courier_name": o["courier_name"],
            "notes": (o["notes"] if "notes" in o.keys() else "") or "",
            "total_cents": int(o["total_cents"] or 0),
            "medio": medio["name"], "ocultar_precios": ocultar,
            "texto_pagado": "TRANSFERIDO" if (medio.get("code") == "transfer") else "PAGADO",
            # compatibilidad con la plantilla anterior
            "payment_method": "transfer" if ocultar else "cash",
        },
        "blocks": bloques,
        "adjustments": ajustes,
        "copies": max(1, min(int(copias), 10)),
        "negocio": {
            "encabezado": cfg["ticket_encabezado"], "pie": cfg["ticket_pie"],
            "nombre": cfg["negocio_nombre"], "telefono": cfg["negocio_telefono"],
            "mostrar_telefono": bool(cfg["ticket_mostrar_telefono_local"]),
            "logo": bool(cfg["ticket_logo"]), "simbolo": cfg["moneda_simbolo"],
        },
    }


# ---------------------------------------------------------------------------
# Marca (logo)
# ---------------------------------------------------------------------------
def ruta_logo_propio() -> Optional[str]:
    d = os.path.join(carpeta_datos(), "marca")
    for ext in ("png", "jpg", "jpeg", "webp"):
        p = os.path.join(d, f"logo.{ext}")
        if os.path.exists(p):
            return p
    return None


_cache_logo: Dict[str, Any] = {}


def logo_escpos() -> bytes:
    """Logo listo para la térmica: el propio (si hay Pillow) o el de fábrica."""
    propio = ruta_logo_propio()
    if propio:
        try:
            clave = f"{propio}:{os.path.getmtime(propio)}"
            if clave not in _cache_logo:
                from PIL import Image  # opcional

                img = Image.open(propio).convert("L")
                lado = 200
                img.thumbnail((lado, lado))
                lienzo = Image.new("L", (((img.width + 7) // 8) * 8, img.height), 255)
                lienzo.paste(img, (0, 0))
                bw = lienzo.point(lambda p: 0 if p < 140 else 255, "1")
                _cache_logo.clear()
                _cache_logo[clave] = _raster(bw)
            return _cache_logo[clave]
        except Exception:
            pass
    return base64.b64decode(LOGO_ESCPOS_B64) if LOGO_ESCPOS_B64 else b""


# ---------------------------------------------------------------------------
# ESC/POS de texto (no depende de nada)
# ---------------------------------------------------------------------------
def ascii_(s: Any) -> str:
    if s is None:
        return ""
    s = str(s)
    for k, v in {"–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "•": "*"}.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).encode("ascii", "ignore").decode("ascii")


def _b(s: str) -> bytes:
    return ascii_(s).encode("ascii", "ignore")


def _lr(izq: str, der: str, ancho: int = ANCHO) -> bytes:
    izq, der = ascii_(izq), ascii_(der)
    if len(izq) + 1 + len(der) > ancho:
        izq = izq[: max(0, ancho - len(der) - 1)]
    return (izq + " " * max(1, ancho - len(izq) - len(der)) + der).encode("ascii", "ignore") + b"\n"


def _sep() -> bytes:
    return b"-" * ANCHO + b"\n"


def unidades(n: Any) -> str:
    """Cantidad de un producto (que no es combo) como la lee el local: "1 unidad", "2 unidades"."""
    n = int(n or 1)
    return f"{n} unidad" if n == 1 else f"{n} unidades"


def _precio(cents: Any, simbolo: str = "$") -> str:
    pesos = f"{int(cents or 0) // 100:,}".replace(",", ".")
    return f"{ascii_(simbolo)} {pesos}"


def _mods(b: Dict[str, Any], ocultar: bool, sim: str, sang: str = "  ") -> bytes:
    out = bytearray()
    for r in b.get("removed") or []:
        out += _b(f"{sang}- Sin {r}") + b"\n"
    for a in b.get("added") or []:
        nombre = f'{sang}+ Extra {a.get("name", "")}' + (f' x{a["count"]}' if a.get("count", 1) > 1 else "")
        out += _b(nombre) + b"\n" if ocultar else _lr(nombre, _precio(a.get("price_cents_total"), sim))
    if not b.get("removed") and not b.get("added"):
        out += _b(f"{sang}Sin modificaciones") + b"\n"
    return bytes(out)


def bytes_escpos_texto(ctx: Dict[str, Any], logo: Optional[bytes] = None) -> bytes:
    o, neg = ctx["order"], ctx["negocio"]
    ocultar, sim = o["ocultar_precios"], neg["simbolo"]

    def uno() -> bytes:
        out = bytearray(ESC + b"@" + ESC + b"a\x01")
        if neg["logo"] and logo:
            out += logo + b"\n"
        if neg["encabezado"]:
            out += GS + b"!\x11" + ESC + b"E\x01" + _b(neg["encabezado"]) + ESC + b"E\x00" + b"\n" + GS + b"!\x00"
        if neg["mostrar_telefono"] and neg["telefono"]:
            out += _b(neg["telefono"]) + b"\n"
        out += b"\n" + ESC + b"a\x00"
        if o["address"]:
            out += ESC + b"E\x01" + _b(o["address"]) + ESC + b"E\x00" + b"\n"
        if o["phone"]:
            out += _b("Tel: " + o["phone"]) + b"\n"
        linea = f'#{o["id"]}  {o["ddmm"]} {o["hhmm"]}' + (f' - Rep: {o["courier_name"]}' if o["courier_name"] else "")
        out += _b(linea) + b"\n" + ESC + b"E\x01" + _b(o["medio"].upper()) + ESC + b"E\x00" + b"\n"
        if o["notes"]:
            out += _b("Nota: " + o["notes"]) + b"\n"
        out += _sep()
        for b in ctx["blocks"]:
            precio = "" if ocultar else _precio(b.get("line_total_cents"), sim)
            if b.get("kind") == "promo":
                out += ESC + b"E\x01" + _lr("PROMO: " + b["promo_name"], precio) + ESC + b"E\x00"
                for c in b["components"]:
                    out += _b("* " + c["name"] + (f' x{c["qty"]}' if c["qty"] > 1 else "")) + b"\n"
                    out += _mods(c, ocultar, sim, "   ")
            else:
                out += ESC + b"E\x01" + _lr(b["name"], precio) + ESC + b"E\x00"
                out += _b(unidades(b["qty"])) + b"\n"
                if not ocultar:
                    out += _lr("  Base", _precio(b["base_price_cents"], sim))
                out += _mods(b, ocultar, sim)
            out += b"\n"
        if ctx["adjustments"] and not ocultar:
            out += _sep()
            for a in ctx["adjustments"]:
                amt = int(a["amount_cents"])
                out += _lr(a["label"], ("- " if amt < 0 else "+ ") + _precio(abs(amt), sim))
        out += _sep() + ESC + b"E\x01" + GS + b"!\x01"
        out += _lr("TOTAL", o["texto_pagado"] if ocultar else _precio(o["total_cents"], sim))
        out += GS + b"!\x00" + ESC + b"E\x00" + b"\n"
        if neg["pie"]:
            out += ESC + b"a\x01" + _b(neg["pie"]) + b"\n" + ESC + b"a\x00"
        out += b"\n\n\n\n" + GS + b"V\x42\x00"
        return bytes(out)

    return uno() * ctx["copies"]


# ---------------------------------------------------------------------------
# Ticket rasterizado en Poppins (requiere Pillow; si no está, se usa el de texto)
# ---------------------------------------------------------------------------
_INV = bytes(255 - i for i in range(256))
_fuentes: Dict[Any, Any] = {}


def _poppins(tam: int, negrita: bool = False) -> Any:
    from PIL import ImageFont

    k = (tam, negrita)
    if k not in _fuentes:
        _fuentes[k] = ImageFont.truetype(io.BytesIO(base64.b64decode(POPPINS_BOLD_B64 if negrita else POPPINS_REG_B64)), tam)
    return _fuentes[k]


def _logo_pil(logo: bytes) -> Any:
    from PIL import Image

    wb, h = logo[4] + logo[5] * 256, logo[6] + logo[7] * 256
    return Image.frombytes("1", (wb * 8, h), logo[8:8 + wb * h].translate(_INV))


def _raster(img: Any) -> bytes:
    w, h = img.size
    wb = (w + 7) // 8
    out, y = bytearray(), 0
    while y < h:
        hh = min(128, h - y)
        tira = img.crop((0, y, wb * 8, y + hh)).tobytes().translate(_INV)
        out += GS + b"v0" + bytes([0, wb & 0xFF, (wb >> 8) & 0xFF, hh & 0xFF, (hh >> 8) & 0xFF]) + tira
        y += hh
    return bytes(out)


def imagen_ticket(ctx: Dict[str, Any], logo: Optional[bytes]) -> Any:
    from PIL import Image, ImageDraw

    o, neg = ctx["order"], ctx["negocio"]
    ocultar, sim = o["ocultar_precios"], neg["simbolo"]
    W, ML, MR = ANCHO_PX, 18, 18
    f_big, f_dir, f_reg = _poppins(60, True), _poppins(25, True), _poppins(23)
    f_item, f_sub, f_pago = _poppins(26, True), _poppins(22), _poppins(22, True)
    f_total, f_pie = _poppins(42, True), _poppins(20, True)
    img = Image.new("L", (W, 14000), 255)
    dr = ImageDraw.Draw(img)
    st = {"y": 12}

    def alto(f: Any) -> int:
        a, d = f.getmetrics()
        return a + d

    def envolver(t: str, f: Any, maxw: float) -> List[str]:
        lineas: List[str] = []
        for palabra in t.split(" "):
            if lineas and dr.textlength(lineas[-1] + " " + palabra, font=f) <= maxw:
                lineas[-1] += " " + palabra
            else:
                lineas.append(palabra)
        out = []
        for ln in lineas:
            while ln and dr.textlength(ln, font=f) > maxw:
                corte = len(ln)
                while corte > 1 and dr.textlength(ln[:corte], font=f) > maxw:
                    corte -= 1
                out.append(ln[:corte])
                ln = ln[corte:]
            out.append(ln)
        return out or [""]

    def sangria(t: str, f: Any) -> Any:
        n = len(t) - len(t.lstrip(" "))
        return (int(n * dr.textlength(" ", font=f)), t[n:])

    def izq(t: str, f: Any, gap: int = 5) -> None:
        ind, txt = sangria(ascii_(t), f)
        for ln in envolver(txt, f, (W - MR) - (ML + ind)):
            dr.text((ML + ind, st["y"]), ln, font=f, fill=0)
            st["y"] += alto(f)
        st["y"] += gap

    def lr(l: str, r: str, fl: Any, fr: Any = None, gap: int = 5) -> None:
        fr = fr or fl
        ind, txt = sangria(ascii_(l), fl)
        rr = ascii_(r)
        pw = dr.textlength(rr, font=fr) if rr else 0
        y0 = st["y"]
        for ln in envolver(txt, fl, (W - MR) - (ML + ind) - ((pw + 14) if rr else 0)):
            dr.text((ML + ind, st["y"]), ln, font=fl, fill=0)
            st["y"] += alto(fl)
        if rr:
            dr.text((W - MR - pw, y0), rr, font=fr, fill=0)
        st["y"] += gap

    def centro(t: str, f: Any, gap: int = 8) -> None:
        tt = ascii_(t)
        dr.text(((W - dr.textlength(tt, font=f)) / 2, st["y"]), tt, font=f, fill=0)
        st["y"] += alto(f) + gap

    def guiones(gap: int = 10) -> None:
        st["y"] += 4
        x = ML
        while x < W - MR:
            dr.line([(x, st["y"]), (min(x + 8, W - MR), st["y"])], fill=0, width=2)
            x += 14
        st["y"] += gap

    if neg["logo"] and logo:
        lg = _logo_pil(logo).convert("L")
        img.paste(lg, ((W - lg.width) // 2, st["y"]))
        st["y"] += lg.height + 2
    if neg["encabezado"]:
        centro(neg["encabezado"], f_big, 10)
    if neg["mostrar_telefono"] and neg["telefono"]:
        centro(neg["telefono"], f_reg)
    if o["address"]:
        izq(o["address"], f_dir)
    if o["phone"]:
        izq("Tel: " + o["phone"], f_reg)
    izq(f'#{o["id"]}  {o["ddmm"]} {o["hhmm"]}' + (f'  -  Rep: {o["courier_name"]}' if o["courier_name"] else ""), f_reg)
    izq(o["medio"].upper(), f_pago)
    if o["notes"]:
        izq("Nota: " + o["notes"], f_pago)
    guiones()

    def mods(b: Dict[str, Any], s: str = "   ") -> None:
        for r in b.get("removed") or []:
            izq(f"{s}- Sin {r}", f_sub)
        for a in b.get("added") or []:
            n = f'{s}+ Extra {a["name"]}' + (f' x{a["count"]}' if a["count"] > 1 else "")
            if ocultar:
                izq(n, f_sub)
            else:
                lr(n, _precio(a["price_cents_total"], sim), f_sub)
        if not b.get("removed") and not b.get("added"):
            izq(f"{s}Sin modificaciones", f_sub)

    for b in ctx["blocks"]:
        precio = "" if ocultar else _precio(b["line_total_cents"], sim)
        if b.get("kind") == "promo":
            lr("PROMO: " + b["promo_name"], precio, f_item)
            for c in b["components"]:
                izq("* " + c["name"] + (f' x{c["qty"]}' if c["qty"] > 1 else ""), f_sub)
                mods(c, "    ")
        else:
            lr(b["name"], precio, f_item)
            izq(unidades(b["qty"]), f_sub)
            if not ocultar:
                lr("   Base", _precio(b["base_price_cents"], sim), f_sub)
            mods(b)
        st["y"] += 8
    if ctx["adjustments"] and not ocultar:
        guiones()
        for a in ctx["adjustments"]:
            amt = int(a["amount_cents"])
            lr(a["label"], ("- " if amt < 0 else "+ ") + _precio(abs(amt), sim), f_sub)
    guiones()
    lr("TOTAL", o["texto_pagado"] if ocultar else _precio(o["total_cents"], sim), f_total)
    st["y"] += 6
    if neg["pie"]:
        centro(neg["pie"], f_pie)
    return img.crop((0, 0, W, st["y"] + 14)).point(lambda p: 0 if p < 128 else 255, "1")


def bytes_escpos(ctx: Dict[str, Any]) -> Any:
    """(bytes, modo). Intenta el ticket en Poppins; si no hay Pillow, texto."""
    logo = logo_escpos()
    try:
        img = imagen_ticket(ctx, logo)
        uno = ESC + b"@" + ESC + b"a\x01" + _raster(img) + ESC + b"a\x00" + b"\n\n\n\n" + GS + b"V\x42\x00"
        return uno * ctx["copies"], "poppins"
    except Exception:
        return bytes_escpos_texto(ctx, logo), "texto"


def bytes_prueba(negocio: str) -> bytes:
    return (ESC + b"@" + ESC + b"a\x01" + GS + b"!\x11" + _b(negocio) + GS + b"!\x00" + b"\n\n"
            + _b("Prueba de impresion OK") + b"\n" + _b("Si lees esto, la impresora esta bien configurada.")
            + b"\n\n\n\n" + GS + b"V\x42\x00")


# ---------------------------------------------------------------------------
# Impresoras de Windows (ctypes: no requiere instalar nada)
# ---------------------------------------------------------------------------
def impresora_predeterminada() -> Optional[str]:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        fn = ctypes.WinDLL("winspool.drv").GetDefaultPrinterW
        fn.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        fn.restype = wintypes.BOOL
        n = wintypes.DWORD(0)
        fn(None, ctypes.byref(n))
        if n.value == 0:
            return None
        buf = ctypes.create_unicode_buffer(n.value)
        return buf.value if fn(buf, ctypes.byref(n)) and buf.value else None
    except Exception:
        return None


def listar_impresoras() -> List[str]:
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        class PRINTER_INFO_4(ctypes.Structure):
            _fields_ = [("pPrinterName", wintypes.LPWSTR), ("pServerName", wintypes.LPWSTR),
                        ("Attributes", wintypes.DWORD)]

        enum = ctypes.WinDLL("winspool.drv").EnumPrintersW
        enum.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
        enum.restype = wintypes.BOOL
        flags = 0x2 | 0x4  # locales + conexiones de red
        need, ret = wintypes.DWORD(0), wintypes.DWORD(0)
        enum(flags, None, 4, None, 0, ctypes.byref(need), ctypes.byref(ret))
        if need.value == 0:
            return []
        buf = ctypes.create_string_buffer(need.value)
        if not enum(flags, None, 4, buf, need, ctypes.byref(need), ctypes.byref(ret)):
            return []
        arr = ctypes.cast(buf, ctypes.POINTER(PRINTER_INFO_4))
        return sorted({arr[i].pPrinterName for i in range(ret.value) if arr[i].pPrinterName})
    except Exception:
        return []


def enviar_crudo(impresora: str, datos: bytes) -> None:
    import ctypes
    from ctypes import wintypes

    ws = ctypes.WinDLL("winspool.drv")

    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [("pDocName", wintypes.LPWSTR), ("pOutputFile", wintypes.LPWSTR), ("pDatatype", wintypes.LPWSTR)]

    ws.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), wintypes.LPVOID]
    ws.OpenPrinterW.restype = wintypes.BOOL
    ws.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(DOC_INFO_1)]
    ws.StartDocPrinterW.restype = wintypes.DWORD
    ws.StartPagePrinter.argtypes = [wintypes.HANDLE]
    ws.WritePrinter.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    ws.WritePrinter.restype = wintypes.BOOL
    ws.EndPagePrinter.argtypes = [wintypes.HANDLE]
    ws.EndDocPrinter.argtypes = [wintypes.HANDLE]
    ws.ClosePrinter.argtypes = [wintypes.HANDLE]

    h = wintypes.HANDLE()
    if not ws.OpenPrinterW(impresora, ctypes.byref(h), None):
        raise OSError(f"No pude abrir la impresora «{impresora}». Revisá que esté encendida y conectada.")
    try:
        if not ws.StartDocPrinterW(h, 1, ctypes.byref(DOC_INFO_1("Ticket", None, "RAW"))):
            raise OSError("La impresora no aceptó el trabajo.")
        try:
            ws.StartPagePrinter(h)
            buf = ctypes.create_string_buffer(datos, len(datos))
            escrito = wintypes.DWORD(0)
            if not ws.WritePrinter(h, buf, len(datos), ctypes.byref(escrito)):
                raise OSError("No se pudo mandar el ticket a la impresora.")
            ws.EndPagePrinter(h)
        finally:
            ws.EndDocPrinter(h)
    finally:
        ws.ClosePrinter(h)


def imprimir(conn: sqlite3.Connection, datos: bytes) -> str:
    """Manda bytes a la impresora configurada (o la predeterminada). Devuelve su nombre."""
    if sys.platform != "win32":
        raise OSError("La impresión directa solo funciona en Windows.")
    impresora = (config.obtener(conn, "printer_name") or "").strip() or impresora_predeterminada() or ""
    if not impresora:
        raise OSError("No hay impresora configurada ni predeterminada.")
    enviar_crudo(impresora, datos)
    return impresora
