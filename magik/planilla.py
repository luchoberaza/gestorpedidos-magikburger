"""Planilla de costos (P-019): se descarga, se completa en Excel y se sube desde Configuración.

Responsable (14-nov): "que descargue la planilla con la que le vamos a tomar lo que vale cada
cosa, la carne […] y por ende también de qué está constituida cada hamburguesa […] la planilla
esa de Excel se tiene que entender bien […] si él no ingresa nada en esa fila, significa que esa
fila no lleva nada" y "el software se comunica bien con los datos de la planilla […] es todo en
uno […] no puede haber inconsistencias".

Por eso la planilla NO es un dato aparte: se arma con lo que ya hay en la base (materia prima,
recetas, ingredientes de la caja) y al subirla escribe en esas mismas tablas, las que usan el
stock, el costo y la ganancia. Descargar → subir sin tocar nada deja todo igual.

Hojas:
  · Cómo completar.
  · Materia prima: lo que se compra, en qué unidad y cuánto cuesta esa unidad (la carne, por kg y
    con el peso de cada hamburguesa).
  · Qué lleva cada producto: una tabla por producto, con sus ingredientes de la caja ya puestos.
  · Extras: cuánto suma cada ingrediente cuando se agrega en la caja (Extra carne = 1 hamburguesa).

Se lee y se escribe sin librerías nuevas (zip + XML), como el resto de los Excel de la app: la PC
del local recibe esto por la actualización automática, que no instala dependencias.
"""
from __future__ import annotations

import io
import re
import sqlite3
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from .dominio import stock
from .dominio.pedidos import pesos_a_centavos
from .dominio.unidades import (PESO_HAMBURGUESA_G, a_compra, a_receta, admite_hamburguesa, es_carne, normalizar,
                               unidad_de_compra, unidad_receta)

HOJA_GUIA = "Cómo completar"
HOJA_MP = "Materia prima"
HOJA_REC = "Qué lleva cada producto"
HOJA_EXT = "Extras"
FILA_ENC = 4                 # fila de los títulos de las columnas (las de arriba explican)
LIBRES_MP = 15               # filas vacías para agregar materia prima
LIBRES_PRODUCTO = 2          # filas vacías por producto (el pan, la caja…)
MAX_DESCOMPRIMIDO = 40 * 1024 * 1024

AVISO_VACIO = "Si dejás una fila sin completar, significa que no lleva nada: esa fila no se usa."


class PlanillaInvalida(Exception):
    def __init__(self, errores: List[str]):
        super().__init__("; ".join(errores))
        self.errores = errores


# ---------------------------------------------------------------------------
# Lo que hay hoy en la base
# ---------------------------------------------------------------------------
def _sin_extra(nombre: str) -> str:
    """'Extra cheddar' → 'Cheddar': el extra usa la misma materia prima que el ingrediente."""
    s = re.sub(r"^\s*(extras?|adicional(es)?)\s+(de\s+)?", "", str(nombre or ""), flags=re.I).strip()
    return (s[:1].upper() + s[1:]) if s else str(nombre or "").strip()


def _num_celda(x: Optional[float]) -> Any:
    if x is None:
        return None
    x = round(float(x), 4)
    return int(x) if x.is_integer() else x


def datos_actuales(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Lo que va pre-cargado en la planilla, sacado de la base del local."""
    insumos = conn.execute("SELECT * FROM insumos ORDER BY nombre COLLATE NOCASE;").fetchall()
    por_id = {int(i["id"]): i for i in insumos}
    ligados = {int(r["ingredient_id"]): (int(r["insumo_id"]), float(r["cantidad"] or 0))
               for r in conn.execute("SELECT * FROM ingrediente_insumo;").fetchall()}
    ingredientes = conn.execute("SELECT * FROM ingredients WHERE active=1 ORDER BY name COLLATE NOCASE;").fetchall()

    def mp_de_ingrediente(ing: Any) -> str:
        lig = ligados.get(int(ing["id"]))
        if lig and lig[0] in por_id:
            return por_id[lig[0]]["nombre"]
        return _sin_extra(ing["name"])

    # Materia prima: la de Inventario + la que sale de los ingredientes de la caja.
    materia: Dict[str, Dict[str, Any]] = {}
    for i in insumos:
        if not int(i["activo"]) and normalizar(i["nombre"]) in materia:
            continue
        if int(i["activo"]):
            materia[normalizar(i["nombre"])] = {
                "nombre": i["nombre"], "unidad": i["unidad"], "costo_cents": int(i["costo_unit_cents"] or 0),
                "porcion_g": float(i["porcion_g"] or 0), "existe": True}
    for ing in ingredientes:
        nombre = mp_de_ingrediente(ing)
        clave = normalizar(nombre)
        if clave and clave not in materia:
            carne = es_carne(nombre)
            materia[clave] = {"nombre": nombre, "unidad": "kg" if carne else "", "costo_cents": None,
                              "porcion_g": PESO_HAMBURGUESA_G if carne else 0.0, "existe": False}

    def cuenta_en(nombre: str) -> str:
        m = materia.get(normalizar(nombre))
        if not nombre:
            return ""
        if m is None:
            return f"no está en «{HOJA_MP}»"
        return unidad_receta(m["unidad"], m["porcion_g"])["texto"] if m["unidad"] else f"falta la unidad en «{HOJA_MP}»"

    base: Dict[int, List[int]] = {}
    for r in conn.execute("SELECT product_id, ingredient_id FROM product_base_ingredients;").fetchall():
        base.setdefault(int(r["product_id"]), []).append(int(r["ingredient_id"]))
    ing_por_id = {int(i["id"]): i for i in ingredientes}
    recetas: Dict[int, List[Any]] = {}
    for r in conn.execute("SELECT r.product_id, r.insumo_id, r.cantidad FROM recetas r;").fetchall():
        i = por_id.get(int(r["insumo_id"]))
        if i is not None:
            recetas.setdefault(int(r["product_id"]), []).append(
                (i["nombre"], a_receta(r["cantidad"], i["unidad"], i["porcion_g"])))

    productos = []
    for p in conn.execute("SELECT * FROM products WHERE active=1 ORDER BY sort_order, name COLLATE NOCASE;"):
        lineas = [(n, c) for n, c in sorted(recetas.get(int(p["id"]), []), key=lambda x: normalizar(x[0]))]
        vistos = {normalizar(n) for n, _ in lineas}
        for iid in base.get(int(p["id"]), []):
            ing = ing_por_id.get(iid)
            if ing is None:
                continue
            n = mp_de_ingrediente(ing)
            if normalizar(n) not in vistos:
                vistos.add(normalizar(n))
                lineas.append((n, None))
        productos.append({"nombre": p["name"], "lineas": [(n, c, cuenta_en(n)) for n, c in lineas]})

    extras = []
    for ing in sorted(ingredientes, key=lambda i: (int(i["extra_price_cents"] or 0) <= 0, normalizar(i["name"]))):
        lig = ligados.get(int(ing["id"]))
        n = mp_de_ingrediente(ing)
        cant = None
        if lig and lig[0] in por_id and lig[1] > 0:
            i = por_id[lig[0]]
            cant = a_receta(lig[1], i["unidad"], i["porcion_g"])
        extras.append({"nombre": ing["name"], "precio_cents": int(ing["extra_price_cents"] or 0),
                       "materia": n, "cantidad": cant, "cuenta_en": cuenta_en(n)})

    return {"materia": sorted(materia.values(), key=lambda m: normalizar(m["nombre"])),
            "productos": productos, "extras": extras}


# ---------------------------------------------------------------------------
# Escribir el .xlsx
# ---------------------------------------------------------------------------
# Estilos (índice en cellXfs)
E_NORMAL, E_ENC, E_TITULO, E_AYUDA, E_EDIT, E_EDIT_NUM, E_EDIT_PLATA, E_INFO, E_PROD_A, E_PROD_B, E_GUIA, E_GUIA_B = range(12)

_ESTILOS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="&quot;{sim}&quot; #,##0.00"/></numFmts>'
    '<fonts count="6"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
    '<font><b/><sz val="15"/><color rgb="FF1F2937"/><name val="Calibri"/></font>'
    '<font><i/><sz val="10"/><color rgb="FF4B5563"/><name val="Calibri"/></font>'
    '<font><sz val="10"/><color rgb="FF6B7280"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><color rgb="FF1F2937"/><name val="Calibri"/></font></fonts>'
    '<fills count="7"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF2F6BEF"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF4C2"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF3F4F6"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFE8EEFD"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF5F3FF"/></patternFill></fill></fills>'
    '<borders count="2"><border/><border><left style="thin"><color rgb="FFD1D5DB"/></left>'
    '<right style="thin"><color rgb="FFD1D5DB"/></right><top style="thin"><color rgb="FFD1D5DB"/></top>'
    '<bottom style="thin"><color rgb="FFD1D5DB"/></bottom><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="12">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="49" fontId="0" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyFill="1" applyBorder="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyFill="1" applyBorder="1"/>'
    '<xf numFmtId="0" fontId="4" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>'
    '<xf numFmtId="0" fontId="5" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>'
    '<xf numFmtId="0" fontId="5" fillId="6" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="5" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>')


def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _celda(ref: str, valor: Any, estilo: int, formula: Optional[str] = None) -> str:
    st = f' s="{estilo}"' if estilo else ""
    if formula:  # el valor va como resultado guardado; Excel la recalcula al abrir
        return f'<c r="{ref}" t="str"{st}><f>{escape(formula)}</f><v>{escape(str(valor or ""))}</v></c>'
    if valor is None or valor == "":
        return f'<c r="{ref}"{st}/>'
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return f'<c r="{ref}"{st}><v>{valor}</v></c>'
    return f'<c r="{ref}" t="inlineStr"{st}><is><t xml:space="preserve">{escape(str(valor))}</t></is></c>'


def _hoja_xml(filas: List[List[Any]], anchos: List[float], congelar: Optional[int] = None,
              validaciones: Optional[List[str]] = None, alturas: Optional[Dict[int, float]] = None) -> str:
    """filas: [[(valor, estilo) | (valor, estilo, fórmula con {n} = n° de fila) | None, …]] desde la fila 1."""
    xml = []
    for n, fila in enumerate(filas, start=1):
        alto = f' ht="{alturas[n]}" customHeight="1"' if alturas and n in alturas else ""
        celdas = "".join(_celda(f"{_col(j)}{n}", c[0], c[1], c[2].replace("{n}", str(n)) if len(c) > 2 else None)
                         for j, c in enumerate(fila) if c is not None)
        xml.append(f'<row r="{n}"{alto}>{celdas}</row>')
    vista = ('<sheetViews><sheetView workbookViewId="0">'
             + (f'<pane ySplit="{congelar}" topLeftCell="A{congelar + 1}" activePane="bottomLeft" state="frozen"/>'
                if congelar else "") + '</sheetView></sheetViews>')
    cols = "".join(f'<col min="{j + 1}" max="{j + 1}" width="{w}" customWidth="1"/>' for j, w in enumerate(anchos))
    dv = (f'<dataValidations count="{len(validaciones)}">{"".join(validaciones)}</dataValidations>'
          if validaciones else "")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'{vista}<cols>{cols}</cols><sheetData>{"".join(xml)}</sheetData>{dv}</worksheet>')


def _lista(sqref: str, formula: str, titulo: str, mensaje: str) -> str:
    return (f'<dataValidation type="list" allowBlank="1" showErrorMessage="1" errorTitle="{escape(titulo)}" '
            f'error="{escape(mensaje)}" sqref="{sqref}"><formula1>{escape(formula)}</formula1></dataValidation>')


def _formula_cuenta_en(col: str, rango: str) -> str:
    """«Se cuenta en» que sigue a la hoja Materia prima: si ahí cambian la unidad o ponen el peso de la
    hamburguesa, la columna se actualiza sola. Mismas reglas que unidad_receta()."""
    u = f"(VLOOKUP({col}{{n}},{rango},2,FALSE)&\"\")"
    p = f"N(VLOOKUP({col}{{n}},{rango},4,FALSE))"
    return (f'IF({col}{{n}}="","",IFERROR('
            f'IF(AND(OR({u}="kg",{u}="g"),{p}>0),"hamburguesas de "&{p}&" g",'
            f'IF(OR({u}="kg",{u}="g"),"gramos",IF(OR({u}="l",{u}="ml"),"ml",'
            f'IF({u}="","falta la unidad en «{HOJA_MP}»",{u})))),"no está en «{HOJA_MP}»"))')


def _numero(sqref: str, mensaje: str) -> str:
    return (f'<dataValidation type="decimal" operator="greaterThanOrEqual" allowBlank="1" showErrorMessage="1" '
            f'errorTitle="Solo el número" error="{escape(mensaje)}" sqref="{sqref}"><formula1>0</formula1>'
            f'</dataValidation>')


def generar(conn: sqlite3.Connection, unidades: List[str], negocio: str = "", simbolo: str = "$") -> bytes:
    d = datos_actuales(conn)
    hoy = datetime.now().strftime("%d/%m/%Y")
    peso = int(PESO_HAMBURGUESA_G)

    # --- Cómo completar
    pasos = [
        (f"Planilla de costos{' — ' + negocio if negocio else ''}", E_TITULO),
        (f"Descargada el {hoy} con tus productos e ingredientes. Completala acá y subila en "
         f"Configuración → Cargar archivos.", E_AYUDA),
        None,
        ("1. Materia prima (lo que comprás)", E_GUIA_B),
        ("Poné en qué unidad lo comprás (kg, g, l, ml, unidades…) y cuánto te cuesta esa unidad. "
         "Ejemplo: Carne · kg · 9500 = el kilo de carne cuesta $ 9.500.", E_GUIA),
        (f"La carne se compra por kg pero se cuenta por hamburguesas: cada una pesa {peso} g (columna «Peso de "
         f"cada hamburguesa»). Por cada hamburguesa que se vende se descuentan {peso} g del stock.", E_GUIA),
        None,
        ("2. Qué lleva cada producto", E_GUIA_B),
        ("Cada producto tiene sus filas, con los ingredientes que ya tiene en la caja. Escribí cuánto lleva UNA "
         "unidad del producto:", E_GUIA),
        ("   · lo que comprás por kg, en gramos (ej.: panceta 30);", E_GUIA),
        (f"   · la carne, en hamburguesas (simple = 1, doble = 2, triple = 3; cada una son {peso} g);", E_GUIA),
        ("   · lo que comprás por litro, en ml; y lo demás, en su unidad (ej.: pan 1, cheddar 2 fetas).", E_GUIA),
        ("Para agregar algo que falta (el pan, la caja, las papas…), usá una fila vacía del producto y elegí la "
         "materia prima de la lista. Si no está, agregala primero en la hoja «Materia prima».", E_GUIA),
        None,
        ("3. Extras", E_GUIA_B),
        ("Cuánto suma cada ingrediente cuando en la caja se agrega como extra. Ejemplo: Extra carne = 1 "
         "hamburguesa. Se suma al costo de esa venta y se descuenta del stock.", E_GUIA),
        ("Cuando se pide «sin …» (ej.: sin cebolla), el programa saca del costo y del stock lo que lleva ese "
         "producto.", E_GUIA),
        None,
        ("Importante", E_GUIA_B),
        (AVISO_VACIO, E_GUIA),
        ("Las celdas amarillas son para completar. No cambies los nombres de las hojas ni de las columnas.", E_GUIA),
        ("Al subirla, el programa revisa todo: si algo no se entiende te dice en qué fila está y no cambia nada. "
         "Lo que subas es lo mismo que ves en Inventario y lo que usa Reportes para calcular la ganancia.", E_GUIA),
    ]
    guia = [[p] if p else [] for p in pasos]

    # --- Materia prima
    mp = [[(f"{HOJA_MP} — lo que comprás", E_TITULO)],
          [("Una fila por cada cosa que comprás. La carne lleva el peso de cada hamburguesa.", E_AYUDA)],
          [(AVISO_VACIO, E_AYUDA)],
          [("Materia prima", E_ENC), ("Se compra por", E_ENC), (f"Costo de esa unidad ({simbolo})", E_ENC),
           ("Peso de cada hamburguesa (g) — solo la carne", E_ENC)]]
    for m in d["materia"]:
        costo = m["costo_cents"] / 100 if m["costo_cents"] else None
        mp.append([(m["nombre"], E_EDIT), (m["unidad"] or None, E_EDIT), (_num_celda(costo), E_EDIT_PLATA),
                   (_num_celda(m["porcion_g"]) if m["porcion_g"] else None, E_EDIT_NUM)])
    for _ in range(LIBRES_MP):
        mp.append([(None, E_EDIT), (None, E_EDIT), (None, E_EDIT_PLATA), (None, E_EDIT_NUM)])
    fin_mp = len(mp)
    lista_mp = f"'{HOJA_MP}'!$A${FILA_ENC + 1}:$A${fin_mp}"
    rango_mp = f"'{HOJA_MP}'!$A${FILA_ENC + 1}:$D${fin_mp}"
    f_rec, f_ext = _formula_cuenta_en("B", rango_mp), _formula_cuenta_en("C", rango_mp)
    val_mp = [
        _lista(f"B{FILA_ENC + 1}:B{fin_mp}", '"' + ",".join(unidades)[:250] + '"', "Unidad",
               "Elegí la unidad de la lista (kg, g, l, ml, unidades…)."),
        _numero(f"C{FILA_ENC + 1}:D{fin_mp}", "Escribí solo el número, sin el signo $ ni letras."),
    ]

    # --- Qué lleva cada producto
    rec = [[(f"{HOJA_REC} — para una unidad", E_TITULO)],
           [("Cuánto lleva UNA unidad de cada producto. Lo que se compra por kg va en gramos; la carne, en "
             "hamburguesas (una doble = 2).", E_AYUDA)],
           [("Si dejás la cantidad vacía, ese producto no lleva eso. Para agregar algo que falta, usá una fila "
             "vacía del producto.", E_AYUDA)],
           [("Producto", E_ENC), ("Materia prima", E_ENC), ("Cantidad", E_ENC), ("Se cuenta en", E_ENC)]]
    for k, p in enumerate(d["productos"]):
        e_prod = E_PROD_A if k % 2 == 0 else E_PROD_B
        for nombre, cant, en in p["lineas"]:
            rec.append([(p["nombre"], e_prod), (nombre, E_EDIT), (_num_celda(cant), E_EDIT_NUM), (en, E_INFO, f_rec)])
        for _ in range(LIBRES_PRODUCTO):
            rec.append([(p["nombre"], e_prod), (None, E_EDIT), (None, E_EDIT_NUM), ("", E_INFO, f_rec)])
    fin_rec = max(len(rec), FILA_ENC + 1)
    val_rec = [
        _lista(f"B{FILA_ENC + 1}:B{fin_rec}", lista_mp, "Materia prima",
               "Elegí una materia prima de la lista. Si falta, agregala primero en la hoja «Materia prima»."),
        _numero(f"C{FILA_ENC + 1}:C{fin_rec}", "Escribí solo el número (sin «g» ni «kg»)."),
    ]

    # --- Extras
    ext = [[(f"{HOJA_EXT} — lo que se agrega en la caja", E_TITULO)],
           [("Cuánto suma cada ingrediente cuando se agrega como extra (ej.: Extra carne = 1 hamburguesa). "
             "Cuando se pide «sin …», se saca lo que lleva el producto.", E_AYUDA)],
           [("Si dejás la cantidad vacía, ese extra no suma costo ni stock.", E_AYUDA)],
           [("Ingrediente (como sale en la caja)", E_ENC), ("Se cobra", E_ENC), ("Materia prima", E_ENC),
            ("Cantidad", E_ENC), ("Se cuenta en", E_ENC)]]
    for x in d["extras"]:
        cobra = f"{simbolo} {x['precio_cents'] / 100:,.0f}".replace(",", ".") if x["precio_cents"] else "sin cargo"
        ext.append([(x["nombre"], E_PROD_A), (cobra, E_INFO), (x["materia"], E_EDIT),
                    (_num_celda(x["cantidad"]), E_EDIT_NUM), (x["cuenta_en"], E_INFO, f_ext)])
    fin_ext = max(len(ext), FILA_ENC + 1)
    val_ext = [
        _lista(f"C{FILA_ENC + 1}:C{fin_ext}", lista_mp, "Materia prima",
               "Elegí una materia prima de la lista. Si falta, agregala primero en la hoja «Materia prima»."),
        _numero(f"D{FILA_ENC + 1}:D{fin_ext}", "Escribí solo el número (sin «g» ni «kg»)."),
    ]

    hojas = [
        (HOJA_GUIA, _hoja_xml(guia, [118], alturas={1: 24})),
        (HOJA_MP, _hoja_xml(mp, [34, 16, 24, 26], FILA_ENC, val_mp, {FILA_ENC: 32})),
        (HOJA_REC, _hoja_xml(rec, [30, 30, 12, 24], FILA_ENC, val_rec, {FILA_ENC: 20})),
        (HOJA_EXT, _hoja_xml(ext, [34, 12, 30, 12, 24], FILA_ENC, val_ext, {FILA_ENC: 20})),
    ]
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
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<bookViews><workbookView activeTab="0"/></bookViews><sheets>'
                   + "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                             for i, (n, _) in enumerate(hojas)) + '</sheets><calcPr fullCalcOnLoad="1"/></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                             for i in range(len(hojas)))
                   + f'<Relationship Id="rId{len(hojas) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   '</Relationships>')
        z.writestr("xl/styles.xml", _ESTILOS.replace("{sim}", escape(simbolo)))
        for i, (_, xml) in enumerate(hojas):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", xml)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Leer el .xlsx (el de la app o el que guarda Excel, LibreOffice o Google)
# ---------------------------------------------------------------------------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _hijos(el: Any, nombre: str) -> List[Any]:
    return [c for c in el if _local(c.tag) == nombre]


def _texto_rico(el: Any) -> str:
    """Texto de un <si> o <is>: el <t> directo o los <r><t> (sin las guías fonéticas)."""
    partes = []
    for c in el:
        n = _local(c.tag)
        if n == "t":
            partes.append(c.text or "")
        elif n == "r":
            partes += [t.text or "" for t in c if _local(t.tag) == "t"]
    return "".join(partes)


def _ref(ref: str) -> Tuple[int, int]:
    m = re.match(r"([A-Za-z]+)(\d+)", ref or "")
    if not m:
        return -1, -1
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col - 1


def leer_xlsx(datos: bytes) -> Dict[str, Dict[int, Dict[int, Any]]]:
    """{nombre de hoja: {fila: {columna: valor}}}. Texto como str, números como float."""
    try:
        z = zipfile.ZipFile(io.BytesIO(datos))
    except zipfile.BadZipFile:
        raise PlanillaInvalida(["El archivo no es una planilla de Excel (.xlsx). Si la guardaste en otro formato, "
                                "abrila y elegí «Guardar como» → «Libro de Excel (.xlsx)»."])
    with z:
        if sum(i.file_size for i in z.infolist()) > MAX_DESCOMPRIMIDO:
            raise PlanillaInvalida(["La planilla es demasiado grande. Descargá una nueva desde Configuración."])
        nombres = set(z.namelist())
        if "xl/workbook.xml" not in nombres:
            raise PlanillaInvalida(["El archivo no es una planilla de Excel (.xlsx)."])
        try:
            wb = ElementTree.fromstring(z.read("xl/workbook.xml"))
            rels = ElementTree.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            compartidos: List[str] = []
            if "xl/sharedStrings.xml" in nombres:
                ss = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
                compartidos = [_texto_rico(si) for si in ss if _local(si.tag) == "si"]
            destino = {r.get("Id"): r.get("Target") for r in rels}
            hojas: Dict[str, Dict[int, Dict[int, Any]]] = {}
            for s in wb.iter():
                if _local(s.tag) != "sheet":
                    continue
                rid = next((v for k, v in s.attrib.items() if _local(k) == "id"), None)
                t = destino.get(rid) or ""
                ruta = t.lstrip("/") if t.startswith("/") else "xl/" + t
                if ruta not in nombres:
                    continue
                hojas[s.get("name") or ""] = _leer_hoja(z.read(ruta), compartidos)
            return hojas
        except ElementTree.ParseError:
            raise PlanillaInvalida(["La planilla está dañada y no se pudo leer. Descargá una nueva y volvé a "
                                    "completarla."])


def _leer_hoja(xml: bytes, compartidos: List[str]) -> Dict[int, Dict[int, Any]]:
    filas: Dict[int, Dict[int, Any]] = {}
    raiz = ElementTree.fromstring(xml)
    n_fila = 0
    for row in raiz.iter():
        if _local(row.tag) != "row":
            continue
        n_fila = int(row.get("r") or n_fila + 1)
        col = -1
        for c in _hijos(row, "c"):
            f, k = _ref(c.get("r") or "")
            col = k if k >= 0 else col + 1
            tipo = c.get("t") or "n"
            v = next((x.text for x in c if _local(x.tag) == "v"), None)
            if tipo == "s":
                valor: Any = compartidos[int(v)] if v is not None and v.isdigit() and int(v) < len(compartidos) else None
            elif tipo == "inlineStr":
                isel = next((x for x in c if _local(x.tag) == "is"), None)
                valor = _texto_rico(isel) if isel is not None else None
            elif tipo in ("str", "d"):
                valor = v
            elif tipo == "b":
                valor = None
            elif tipo == "e":
                valor = None
            else:
                try:
                    valor = float(v) if v not in (None, "") else None
                except ValueError:
                    valor = None
            if isinstance(valor, str):
                valor = valor.strip() or None
            if valor is not None:
                filas.setdefault(n_fila, {})[col] = valor
    return filas


# ---------------------------------------------------------------------------
# Revisar y aplicar
# ---------------------------------------------------------------------------
def _buscar_hoja(hojas: Dict[str, Any], nombre: str) -> Optional[Dict[int, Dict[int, Any]]]:
    for n, h in hojas.items():
        if normalizar(n) == normalizar(nombre):
            return h
    return None


def _tabla(hoja: Dict[int, Dict[int, Any]], columnas: Dict[str, str], primera: str
           ) -> Optional[Tuple[Dict[str, int], List[Tuple[int, Dict[str, Any]]]]]:
    """Encuentra la fila de los títulos y devuelve (columna → índice, [(n° de fila, {columna: valor})]).
    La fila de títulos es la primera (de las 15 de arriba) que empieza con `primera` y trae al menos
    otro título esperado: así el título de la hoja («Materia prima — lo que comprás») no la confunde."""
    for n in sorted(hoja)[:15]:
        titulos = sorted(((k, normalizar(v)) for k, v in hoja[n].items() if isinstance(v, str)))
        idx: Dict[str, int] = {}
        for clave, prefijo in columnas.items():
            for k, t in titulos:
                if t.startswith(normalizar(prefijo)) and k not in idx.values():
                    idx[clave] = k
                    break
        if len(idx) < 2 or not any(t.startswith(normalizar(primera)) for _, t in titulos):
            continue
        filas = [(m, {c: hoja[m].get(k) for c, k in idx.items()}) for m in sorted(hoja) if m > n]
        return idx, filas
    return None


def _vacio(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _texto(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    return str(v).strip()


def _cantidad(v: Any, en: Dict[str, Any]) -> Optional[float]:
    """Número de la celda. Acepta '120', '0,5' y también '120 g' o '2 hamburguesas' si la unidad coincide."""
    if isinstance(v, (int, float)):
        return float(v)
    s = normalizar(v)
    m = re.match(r"^(-?\d+(?:[.,]\d+)?)\s*(.*)$", s)
    if not m:
        return None
    resto = m.group(2).strip()
    validas = {normalizar(en["singular"]), normalizar(en["plural"]), normalizar(en["texto"])}
    if en["singular"] == "g":
        validas |= {"gr", "grs", "gramo", "gramos"}
    if en["singular"] == "ml":
        validas |= {"cc", "mililitros"}
    if en["singular"] == "unidad":
        validas |= {"u", "un", "unid"}
    if resto and resto not in validas and not resto.startswith("hamburguesa" if en["hamburguesa"] else "\0"):
        return None
    return float(m.group(1).replace(",", "."))


def revisar(conn: sqlite3.Connection, hojas: Dict[str, Any], unidades: List[str]) -> Dict[str, Any]:
    """Interpreta la planilla. Si algo no se entiende, PlanillaInvalida con TODOS los problemas."""
    errores: List[str] = []
    avisos: List[str] = []

    h_mp, h_rec, h_ext = (_buscar_hoja(hojas, n) for n in (HOJA_MP, HOJA_REC, HOJA_EXT))
    for h, n in ((h_mp, HOJA_MP), (h_rec, HOJA_REC)):
        if h is None:
            errores.append(f"Falta la hoja «{n}». Descargá la planilla de nuevo desde Configuración → Cargar "
                           f"archivos y completá esa.")
    if errores:
        raise PlanillaInvalida(errores)

    # --- Materia prima
    t_mp = _tabla(h_mp, {"nombre": "materia prima", "unidad": "se compra", "costo": "costo", "peso": "peso"},
                  "materia prima")
    if t_mp is None or "unidad" not in t_mp[0] or "costo" not in t_mp[0]:
        raise PlanillaInvalida([f"En la hoja «{HOJA_MP}» no están las columnas «Materia prima», «Se compra por» y "
                                f"«Costo». No cambies los títulos de las columnas."])
    existentes = {normalizar(i["nombre"]): i for i in conn.execute(
        "SELECT * FROM insumos ORDER BY activo DESC, id;").fetchall()[::-1]}
    materia: Dict[str, Dict[str, Any]] = {}
    for fila, c in t_mp[1]:
        nombre = _texto(c.get("nombre"))
        unidad_txt, costo_v, peso_v = c.get("unidad"), c.get("costo"), c.get("peso")
        donde = f"Hoja «{HOJA_MP}», fila {fila}"
        if not nombre:
            if not (_vacio(unidad_txt) and _vacio(costo_v) and _vacio(peso_v)):
                errores.append(f"{donde}: falta el nombre de la materia prima.")
            continue
        if len(nombre) > 80:
            errores.append(f"{donde}: el nombre «{nombre[:30]}…» es muy largo (máximo 80 letras).")
            continue
        clave = normalizar(nombre)
        if clave in materia:
            errores.append(f"{donde}: «{nombre}» está dos veces. Dejá una sola fila.")
            continue
        if _vacio(unidad_txt) and _vacio(costo_v) and _vacio(peso_v):
            materia[clave] = {"nombre": nombre, "usar": False, "fila": fila}
            continue
        # Una fila con error queda anotada: las recetas que la usan no repiten el aviso.
        materia[clave] = {"nombre": nombre, "usar": False, "fila": fila, "error": True}
        unidad = unidad_de_compra(unidad_txt, unidades)
        if _vacio(unidad_txt):
            errores.append(f"{donde} ({nombre}): falta en qué unidad se compra (kg, g, l, ml, unidades…).")
            continue
        if unidad is None:
            errores.append(f"{donde} ({nombre}): la unidad «{_texto(unidad_txt)}» no se entiende. Usá una de la "
                           f"lista: {', '.join(unidades)}.")
            continue
        costo = 0
        if not _vacio(costo_v):
            costo = (int(round(float(costo_v) * 100)) if isinstance(costo_v, float)
                     else pesos_a_centavos(costo_v, -1, negativo=True))
            if costo < 0:
                errores.append(f"{donde} ({nombre}): el costo «{_texto(costo_v)}» no es un número válido.")
                continue
        peso = 0.0
        if not _vacio(peso_v):
            x = _cantidad(peso_v, unidad_receta("g"))
            if x is None or x <= 0:
                errores.append(f"{donde} ({nombre}): el peso de la hamburguesa tiene que ser un número en gramos "
                               f"(ej.: 120).")
                continue
            if not admite_hamburguesa(unidad):
                errores.append(f"{donde} ({nombre}): el peso de la hamburguesa va solo si se compra por kg o por g. "
                               f"Borralo o cambiá la unidad.")
                continue
            peso = x
        materia[clave] = {"nombre": nombre, "usar": True, "unidad": unidad, "costo_cents": costo, "porcion_g": peso,
                          "fila": fila}

    def info_materia(texto: str, donde: str) -> Optional[Dict[str, Any]]:
        clave = normalizar(texto)
        m = materia.get(clave)
        if m is not None and m["usar"]:
            return {"clave": clave, "unidad": m["unidad"], "porcion_g": m["porcion_g"], "nombre": m["nombre"]}
        if m is not None and m.get("error"):
            return None  # ya se avisó en su fila de «Materia prima»
        e = existentes.get(clave)
        if e is not None:
            return {"clave": clave, "unidad": e["unidad"], "porcion_g": float(e["porcion_g"] or 0),
                    "nombre": e["nombre"]}
        if m is not None:
            errores.append(f"{donde}: «{texto}» no tiene unidad en la hoja «{HOJA_MP}» (fila {m['fila']}). "
                           f"Completá en qué se compra y cuánto cuesta.")
        else:
            errores.append(f"{donde}: «{texto}» no está en la hoja «{HOJA_MP}». Agregala ahí con su unidad y su "
                           f"costo.")
        return None

    def leer_cant(v: Any, info: Dict[str, Any], donde: str) -> Optional[float]:
        en = unidad_receta(info["unidad"], info["porcion_g"])
        x = _cantidad(v, en)
        if x is None or x < 0:
            errores.append(f"{donde}: la cantidad «{_texto(v)}» tiene que ser un número (en {en['texto']}).")
            return None
        return x

    # --- Qué lleva cada producto
    t_rec = _tabla(h_rec, {"producto": "producto", "materia": "materia prima", "cantidad": "cantidad"}, "producto")
    if t_rec is None or len(t_rec[0]) < 3:
        raise PlanillaInvalida(errores + [f"En la hoja «{HOJA_REC}» no están las columnas «Producto», «Materia "
                                          f"prima» y «Cantidad». No cambies los títulos de las columnas."])
    productos = {}
    for p in conn.execute("SELECT id, name, active FROM products ORDER BY active, id;").fetchall():
        productos[normalizar(p["name"])] = p
    recetas: Dict[int, Dict[str, Any]] = {}
    desconocidos: List[str] = []
    actual: Optional[Any] = None
    nombre_actual = ""
    for fila, c in t_rec[1]:
        prod_txt = _texto(c.get("producto"))
        mp_txt = _texto(c.get("materia"))
        cant_v = c.get("cantidad")
        if prod_txt:
            nombre_actual = prod_txt
            actual = productos.get(normalizar(prod_txt))
            if actual is None:
                if prod_txt not in desconocidos:
                    desconocidos.append(prod_txt)
            else:
                recetas.setdefault(int(actual["id"]), {"nombre": actual["name"], "lineas": {}})
        if not mp_txt and _vacio(cant_v):
            continue
        donde = f"Hoja «{HOJA_REC}», fila {fila}" + (f" ({nombre_actual})" if nombre_actual else "")
        if not nombre_actual:
            errores.append(f"{donde}: falta el producto.")
            continue
        if actual is None:
            continue
        if not mp_txt:
            errores.append(f"{donde}: hay una cantidad pero falta la materia prima.")
            continue
        info = info_materia(mp_txt, donde)
        if info is None or _vacio(cant_v):
            continue
        x = leer_cant(cant_v, info, f"{donde} · {mp_txt}")
        if x is None or x == 0:
            continue
        lineas = recetas[int(actual["id"])]["lineas"]
        if info["clave"] in lineas:
            errores.append(f"{donde}: «{mp_txt}» está dos veces en {actual['name']}. Dejá una sola fila.")
            continue
        lineas[info["clave"]] = {"cantidad": x, **info}
    for n in desconocidos:
        avisos.append(f"«{n}» no está entre los productos de la caja: esas filas no se usaron.")
    en_hoja = set(recetas)
    for p in conn.execute("SELECT id, name FROM products WHERE active=1;").fetchall():
        if int(p["id"]) not in en_hoja:
            avisos.append(f"«{p['name']}» no estaba en la planilla: lo que lleva no se cambió.")

    # --- Extras
    ligues: Dict[int, Optional[Dict[str, Any]]] = {}
    if h_ext is not None:
        t_ext = _tabla(h_ext, {"ingrediente": "ingrediente", "materia": "materia prima", "cantidad": "cantidad"},
                       "ingrediente")
        if t_ext is None or len(t_ext[0]) < 3:
            errores.append(f"En la hoja «{HOJA_EXT}» no están las columnas «Ingrediente», «Materia prima» y "
                           f"«Cantidad». No cambies los títulos de las columnas.")
        else:
            ingredientes = {normalizar(i["name"]): i for i in conn.execute(
                "SELECT id, name FROM ingredients ORDER BY active, id;").fetchall()}
            for fila, c in t_ext[1]:
                ing_txt, mp_txt, cant_v = _texto(c.get("ingrediente")), _texto(c.get("materia")), c.get("cantidad")
                donde = f"Hoja «{HOJA_EXT}», fila {fila}" + (f" ({ing_txt})" if ing_txt else "")
                if not ing_txt:
                    if mp_txt or not _vacio(cant_v):
                        errores.append(f"{donde}: falta el ingrediente.")
                    continue
                ing = ingredientes.get(normalizar(ing_txt))
                if ing is None:
                    avisos.append(f"«{ing_txt}» no está entre los ingredientes de la caja: esa fila no se usó.")
                    continue
                if int(ing["id"]) in ligues:
                    errores.append(f"{donde}: «{ing_txt}» está dos veces. Dejá una sola fila.")
                    continue
                if not mp_txt:
                    if not _vacio(cant_v):
                        errores.append(f"{donde}: hay una cantidad pero falta la materia prima.")
                        continue
                    ligues[int(ing["id"])] = None
                    continue
                info = info_materia(mp_txt, donde)
                if info is None:
                    continue
                x = 0.0
                if not _vacio(cant_v):
                    x = leer_cant(cant_v, info, donde)
                    if x is None:
                        continue
                ligues[int(ing["id"])] = {"cantidad": x, **info}

    if errores:
        raise PlanillaInvalida(errores)
    return {"materia": materia, "existentes": existentes, "recetas": recetas, "ligues": ligues, "avisos": avisos}


def aplicar(conn: sqlite3.Connection, plan: Dict[str, Any]) -> Dict[str, Any]:
    """Escribe la planilla revisada en Inventario (materia prima, recetas y extras). No hace commit."""
    existentes = plan["existentes"]
    avisos = list(plan["avisos"])
    ids: Dict[str, int] = {k: int(e["id"]) for k, e in existentes.items()}
    nuevas = actualizadas = 0
    for clave, m in plan["materia"].items():
        if not m["usar"]:
            continue
        e = existentes.get(clave)
        if e is None:
            cur = conn.execute(
                "INSERT INTO insumos(nombre, categoria, unidad, stock, stock_minimo, costo_unit_cents, activo, porcion_g) "
                "VALUES(?,?,?,0,0,?,1,?);", (m["nombre"], "", m["unidad"], m["costo_cents"], m["porcion_g"]))
            ids[clave] = int(cur.lastrowid)
            nuevas += 1
            continue
        if e["unidad"] != m["unidad"]:
            if stock.convertir_unidad(conn, int(e["id"]), e["unidad"], m["unidad"]) is None:
                avisos.append(f"«{e['nombre']}» pasó de {e['unidad']} a {m['unidad']}: revisá su stock en Inventario.")
        cambio = (e["unidad"] != m["unidad"] or int(e["costo_unit_cents"] or 0) != m["costo_cents"]
                  or float(e["porcion_g"] or 0) != m["porcion_g"] or not int(e["activo"]))
        conn.execute("UPDATE insumos SET unidad=?, costo_unit_cents=?, porcion_g=?, activo=1 WHERE id=?;",
                     (m["unidad"], m["costo_cents"], m["porcion_g"], int(e["id"])))
        actualizadas += 1 if cambio else 0

    con_receta, sin_receta = 0, []
    for pid, r in plan["recetas"].items():
        conn.execute("DELETE FROM recetas WHERE product_id=?;", (pid,))
        for clave, ln in r["lineas"].items():
            conn.execute("INSERT INTO recetas(product_id, insumo_id, cantidad) VALUES(?,?,?);",
                         (pid, ids[clave], a_compra(ln["cantidad"], ln["unidad"], ln["porcion_g"])))
        if r["lineas"]:
            con_receta += 1
        else:
            sin_receta.append(r["nombre"])

    extras = 0
    for ing, ln in plan["ligues"].items():
        conn.execute("DELETE FROM ingrediente_insumo WHERE ingredient_id=?;", (ing,))
        if ln is None:
            continue
        conn.execute("INSERT INTO ingrediente_insumo(ingredient_id, insumo_id, cantidad) VALUES(?,?,?);",
                     (ing, ids[ln["clave"]], a_compra(ln["cantidad"], ln["unidad"], ln["porcion_g"])))
        extras += 1 if ln["cantidad"] > 0 else 0

    from .dominio.costos import costos_por_producto

    costos = costos_por_producto(conn)
    resumen_costos = []
    for pid, r in plan["recetas"].items():
        c = costos.get(pid)
        resumen_costos.append({"nombre": r["nombre"], "costo_cents": c["costo_cents"] if c else None,
                               "faltan": c["faltan"] if c else [],
                               "estado": "sin_receta" if c is None else ("sin_costo" if c["faltan"] else "ok")})
    resumen_costos.sort(key=lambda x: normalizar(x["nombre"]))
    return {"materias_nuevas": nuevas, "materias_actualizadas": actualizadas, "productos_con_receta": con_receta,
            "productos_sin_receta": sin_receta, "extras": extras, "avisos": avisos, "costos": resumen_costos}
