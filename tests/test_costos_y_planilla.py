"""Pedidos del 14-nov (P-015 a P-019): planilla de costos, carne por gramos, ganancia real,
promos aparte y desglose de la liquidación. Corren contra bases temporales.

Uso:  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import io
import os
import sys
import unittest
import zipfile
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_magik import Base  # noqa: E402

from magik import planilla  # noqa: E402
from magik.dominio import unidades as u  # noqa: E402


def xlsx_como_excel(hojas):
    """Arma un .xlsx con textos compartidos (sharedStrings), como lo guarda Excel al completar la planilla.
    hojas: [(nombre, {fila: {columna: valor}})]."""
    textos, indice = [], {}

    def s(v):
        if v not in indice:
            indice[v] = len(textos)
            textos.append(v)
        return indice[v]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        hojas_xml = []
        for i, (nombre, filas) in enumerate(hojas):
            xml = []
            for n in sorted(filas):
                celdas = []
                for k in sorted(filas[n]):
                    v = filas[n][k]
                    ref = planilla._col(k) + str(n)
                    if v is None:
                        continue
                    if isinstance(v, (int, float)):
                        celdas.append(f'<c r="{ref}"><v>{v}</v></c>')
                    else:
                        celdas.append(f'<c r="{ref}" t="s"><v>{s(str(v))}</v></c>')
                xml.append(f'<row r="{n}">{"".join(celdas)}</row>')
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml",
                       '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/'
                       f'spreadsheetml/2006/main"><sheetData>{"".join(xml)}</sheetData></worksheet>')
            hojas_xml.append(f'<sheet name="{escape(nombre)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/'
                   'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
                   f'relationships"><sheets>{"".join(hojas_xml)}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/relationships">' + "".join(
                       f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                       f'relationships/worksheet" Target="/xl/worksheets/sheet{i + 1}.xml"/>' for i in range(len(hojas)))
                   + "</Relationships>")
        z.writestr("xl/sharedStrings.xml",
                   '<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/'
                   'spreadsheetml/2006/main">' + "".join(
                       f'<si><r><t xml:space="preserve">{escape(t)}</t></r></si>' for t in textos) + "</sst>")
    return buf.getvalue()


# ===========================================================================
class TestUnidades(unittest.TestCase):
    """P-016: "la carne se contabilice por gramos, 120g es una hamburguesa, se pueden poner por kg la carne
    que ingreso pero siempre la hamburguesa pesa 120g exactos"."""

    def test_entiende_como_se_escribe_la_unidad(self):
        self.assertEqual(u.unidad_de_compra("Kilos"), "kg")
        self.assertEqual(u.unidad_de_compra("gr"), "g")
        self.assertEqual(u.unidad_de_compra("Litro"), "l")
        self.assertEqual(u.unidad_de_compra("unidad"), "unidades")
        self.assertEqual(u.unidad_de_compra("Fetas", ["unidades", "fetas"]), "fetas")
        self.assertIsNone(u.unidad_de_compra("bolsitas", ["unidades", "fetas"]))

    def test_la_carne_va_en_hamburguesas_de_120_g_y_se_guarda_en_kg(self):
        en = u.unidad_receta("kg", 120)
        self.assertEqual((en["plural"], en["texto"]), ("hamburguesas", "hamburguesas de 120 g"))
        self.assertEqual(u.a_compra(2, "kg", 120), 0.24)          # una doble
        self.assertEqual(u.a_receta(0.24, "kg", 120), 2)
        self.assertEqual(u.unidad_receta("kg")["texto"], "gramos")  # lo demás que se compra por kg, en gramos
        self.assertEqual(u.a_compra(30, "kg"), 0.03)
        self.assertEqual(u.a_compra(50, "l"), 0.05)
        self.assertEqual(u.a_compra(2, "fetas"), 2)
        self.assertEqual(u.conversion("kg", "g"), 1000)
        self.assertIsNone(u.conversion("unidades", "kg"))


# ===========================================================================
class ConCatalogo(Base):
    """Clásica (carne, pan, cheddar, cebolla) $300 · Doble (carne, pan, cheddar) $450 · Papas $200.
    Extras: Extra carne $90 y Extra cheddar $50."""

    def setUp(self):
        super().setUp()
        conn = self.db()
        cur = conn.cursor()
        self.ing = {}
        for n, precio in (("Carne", 0), ("Pan", 0), ("Cheddar", 0), ("Cebolla", 0), ("Extra carne", 9000),
                          ("Extra cheddar", 5000)):
            cur.execute("INSERT INTO ingredients(name, extra_price_cents, active) VALUES(?,?,1);", (n, precio))
            self.ing[n] = cur.lastrowid
        self.prod = {}
        for n, precio, base in (("Clásica", 30000, ["Carne", "Pan", "Cheddar", "Cebolla"]),
                                ("Doble", 45000, ["Carne", "Pan", "Cheddar"]), ("Papas", 20000, [])):
            cur.execute("INSERT INTO products(name, price_cents, active) VALUES(?,?,1);", (n, precio))
            self.prod[n] = cur.lastrowid
            for b in base:
                cur.execute("INSERT INTO product_base_ingredients VALUES(?,?);", (self.prod[n], self.ing[b]))
        cur.execute("INSERT INTO couriers(name, active, cash_float_cents) VALUES('Juan', 1, 150000);")
        self.juan = cur.lastrowid
        cur.execute("INSERT INTO promos(name, price_cents, active) VALUES('Promo pareja', 70000, 1);")
        self.promo = cur.lastrowid
        cur.execute("INSERT INTO promo_components(promo_id, product_id, sort_order) VALUES(?,?,0),(?,?,1);",
                    (self.promo, self.prod["Clásica"], self.promo, self.prod["Doble"]))
        conn.commit()
        conn.close()
        self.admin()

    def descargar(self):
        r = self.c.get("/api/planilla-costos.xlsx")
        self.assertEqual(r.status_code, 200)
        datos = r.data
        r.close()
        return datos

    def hojas(self, datos=None):
        h = planilla.leer_xlsx(datos or self.descargar())
        return [(n, h[n]) for n in h]

    def subir(self, hojas):
        datos = xlsx_como_excel(hojas) if isinstance(hojas, list) else hojas
        return self.c.post("/api/planilla-costos", data={"archivo": (io.BytesIO(datos), "costos.xlsx")},
                           content_type="multipart/form-data")

    @staticmethod
    def buscar(filas, col, valor, desde=5):
        return [n for n in sorted(filas) if n >= desde and filas[n].get(col) == valor]

    def completar(self, hojas):
        """Lo que haría el dueño: costos de la materia prima, cuánto lleva cada producto y los extras."""
        h = dict(hojas)
        mp = h[planilla.HOJA_MP]
        valores = {"Carne": ("kg", 9500), "Pan": ("unidades", 50), "Cheddar": ("unidades", 30),
                   "Cebolla": ("kg", 400)}
        for nombre, (unidad, costo) in valores.items():
            n = self.buscar(mp, 0, nombre)[0]
            mp[n][1], mp[n][2] = unidad, costo
        rec = h[planilla.HOJA_REC]
        cant = {("Clásica", "Carne"): 1, ("Clásica", "Pan"): 1, ("Clásica", "Cheddar"): 1, ("Clásica", "Cebolla"): 20,
                ("Doble", "Carne"): 2, ("Doble", "Pan"): 1, ("Doble", "Cheddar"): 2}
        for n in sorted(rec):
            if n > 4 and (rec[n].get(0), rec[n].get(1)) in cant:
                rec[n][2] = cant[(rec[n][0], rec[n][1])]
        ext = h[planilla.HOJA_EXT]
        for nombre, c in (("Extra carne", 1), ("Extra cheddar", 1)):
            ext[self.buscar(ext, 0, nombre)[0]][3] = c
        return [(n, h[n]) for n, _ in hojas]

    def recetas(self):
        conn = self.db()
        out = {}
        for r in conn.execute("SELECT p.name, i.nombre, r.cantidad FROM recetas r JOIN products p ON p.id=r.product_id "
                              "JOIN insumos i ON i.id=r.insumo_id;"):
            out.setdefault(r["name"], {})[r["nombre"]] = r["cantidad"]
        conn.close()
        return out


# ===========================================================================
class TestPlanillaDeCostos(ConCatalogo):
    """P-019 (responsable, 14-nov): "en la parte de configuración […] un botón que sea cargar archivos […] subir y
    descargar […] la planilla esa de Excel se tiene que entender bien […] si él no ingresa nada en esa fila,
    significa que esa fila no lleva nada" · "el software se comunica bien con los datos de la planilla […] es
    todo en uno […] no puede haber inconsistencias"."""

    def test_viene_con_sus_productos_e_ingredientes(self):
        h = dict(self.hojas())
        self.assertEqual(list(h), [planilla.HOJA_GUIA, planilla.HOJA_MP, planilla.HOJA_REC, planilla.HOJA_EXT])
        mp = h[planilla.HOJA_MP]
        self.assertEqual(mp[4][0], "Materia prima")
        nombres = [mp[n][0] for n in sorted(mp) if n > 4 and 0 in mp[n]]
        self.assertEqual(nombres, ["Carne", "Cebolla", "Cheddar", "Pan"])  # «Extra carne» usa la Carne
        carne = mp[self.buscar(mp, 0, "Carne")[0]]
        self.assertEqual((carne.get(1), carne.get(3)), ("kg", 120))          # la carne: por kg, 120 g c/u
        rec = h[planilla.HOJA_REC]
        clasica = [rec[n].get(1) for n in self.buscar(rec, 0, "Clásica")]
        self.assertEqual(clasica, ["Carne", "Pan", "Cheddar", "Cebolla", None, None])  # sus ingredientes + 2 libres
        carne_cl = rec[self.buscar(rec, 0, "Clásica")[0]]
        self.assertEqual(carne_cl.get(3), "hamburguesas de 120 g")
        self.assertEqual(len(self.buscar(rec, 0, "Papas")), 2)                # sin ingredientes: solo filas libres
        ext = h[planilla.HOJA_EXT]
        fila = ext[self.buscar(ext, 0, "Extra carne")[0]]
        self.assertEqual((fila.get(1), fila.get(2)), ("$ 90", "Carne"))
        guia = " ".join(str(v) for f in h[planilla.HOJA_GUIA].values() for v in f.values())
        self.assertIn("no lleva nada: esa fila no se usa", guia)

    def test_subirla_carga_costos_recetas_y_extras(self):
        r = self.subir(self.completar(self.hojas()))
        self.assertEqual(r.status_code, 200, r.get_json())
        res = r.get_json()["resultado"]
        self.assertEqual((res["materias_nuevas"], res["productos_con_receta"], res["extras"]), (4, 2, 2))
        self.assertEqual(res["productos_sin_receta"], ["Papas"])
        conn = self.db()
        carne = conn.execute("SELECT * FROM insumos WHERE nombre='Carne';").fetchone()
        self.assertEqual((carne["unidad"], carne["costo_unit_cents"], carne["porcion_g"]), ("kg", 950000, 120))
        ligado = conn.execute("SELECT cantidad FROM ingrediente_insumo WHERE ingredient_id=?;",
                              (self.ing["Extra carne"],)).fetchone()[0]
        sin_cantidad = conn.execute("SELECT cantidad FROM ingrediente_insumo WHERE ingredient_id=?;",
                                    (self.ing["Cebolla"],)).fetchone()[0]
        conn.close()
        self.assertEqual(ligado, 0.12)        # Extra carne = 1 hamburguesa = 120 g
        self.assertEqual(sin_cantidad, 0)     # ligada solo para el «sin cebolla»
        rec = self.recetas()
        self.assertEqual(rec["Clásica"], {"Carne": 0.12, "Pan": 1, "Cheddar": 1, "Cebolla": 0.02})
        self.assertEqual(rec["Doble"], {"Carne": 0.24, "Pan": 1, "Cheddar": 2})
        costos = {c["nombre"]: c for c in res["costos"]}
        # Clásica: 0,12 kg × $9.500 + $50 + $30 + 0,02 kg × $400 = $1.228
        self.assertEqual(costos["Clásica"]["costo_cents"], 122800)
        self.assertEqual(costos["Doble"]["costo_cents"], 239000)
        # Lo mismo que muestra Reportes: es todo uno
        f = {x["nombre"]: x for x in self.c.get("/api/reportes").get_json()["ganancia_productos"]["filas"]}
        self.assertEqual(f["Clásica"]["costo_cents"], 122800)
        self.assertEqual(f["Promo pareja"]["costo_cents"], 122800 + 239000)

    def test_lo_que_se_descarga_es_lo_que_se_subio(self):
        self.subir(self.completar(self.hojas()))
        datos = self.descargar()
        h = dict(self.hojas(datos))
        rec = h[planilla.HOJA_REC]
        doble = {rec[n].get(1): rec[n].get(2) for n in self.buscar(rec, 0, "Doble") if rec[n].get(1)}
        self.assertEqual(doble, {"Carne": 2, "Cheddar": 2, "Pan": 1})
        cebolla = [rec[n] for n in self.buscar(rec, 0, "Clásica") if rec[n].get(1) == "Cebolla"][0]
        self.assertEqual((cebolla[2], cebolla[3]), (20, "gramos"))
        mp = h[planilla.HOJA_MP]
        self.assertEqual(mp[self.buscar(mp, 0, "Carne")[0]].get(2), 9500)
        # Subir lo descargado sin tocar nada no cambia nada
        antes = self.recetas()
        self.assertEqual(self.subir(datos).status_code, 200)
        self.assertEqual(self.recetas(), antes)

    def test_fila_vacia_no_lleva_nada(self):
        self.subir(self.completar(self.hojas()))
        h = self.hojas()
        rec = dict(h)[planilla.HOJA_REC]
        n = [n for n in self.buscar(rec, 0, "Doble") if rec[n].get(1) == "Cheddar"][0]
        del rec[n][2]
        self.assertEqual(self.subir(h).status_code, 200)
        self.assertEqual(self.recetas()["Doble"], {"Carne": 0.24, "Pan": 1})

    def test_agregar_lo_que_falta_en_una_fila_libre(self):
        h = self.completar(self.hojas())
        rec = dict(h)[planilla.HOJA_REC]
        libre = [n for n in self.buscar(rec, 0, "Papas")][0]
        rec[libre][1], rec[libre][2] = "Cebolla", 50
        self.assertEqual(self.subir(h).status_code, 200)
        self.assertEqual(self.recetas()["Papas"], {"Cebolla": 0.05})

    def test_si_algo_no_se_entiende_dice_la_fila_y_no_cambia_nada(self):
        self.subir(self.completar(self.hojas()))
        antes = self.recetas()
        h = self.hojas()
        d = dict(h)
        mp, rec = d[planilla.HOJA_MP], d[planilla.HOJA_REC]
        fila_pan = self.buscar(mp, 0, "Pan")[0]
        mp[fila_pan][1] = "bolsitas"
        n = [n for n in self.buscar(rec, 0, "Doble") if rec[n].get(1) == "Carne"][0]
        rec[n][2] = "dos"
        libre = self.buscar(rec, 0, "Papas")[0]
        rec[libre][1], rec[libre][2] = "Ketchup", 10
        r = self.subir(h)
        self.assertEqual(r.status_code, 400)
        errores = r.get_json()["errores"]
        self.assertEqual(len(errores), 3, errores)
        self.assertIn(f"fila {fila_pan} (Pan): la unidad «bolsitas» no se entiende", errores[0])
        self.assertIn(f"fila {n} (Doble) · Carne: la cantidad «dos» tiene que ser un número (en hamburguesas de 120 g)",
                      errores[1])
        self.assertIn("«Ketchup» no está en la hoja «Materia prima»", errores[2])
        self.assertIn("No se cambió nada", r.get_json()["error"])
        self.assertEqual(self.recetas(), antes)

    def test_no_es_una_planilla(self):
        r = self.subir(b"esto no es un excel")
        self.assertEqual(r.status_code, 400)
        self.assertIn("no es una planilla de Excel", r.get_json()["error"])

    def test_la_caja_no_la_ve(self):
        self.c.post("/api/logout")
        self.assertEqual(self.c.post("/api/login", json={"username": "caja", "password": ""}).status_code, 200)
        self.assertEqual(self.c.get("/api/planilla-costos.xlsx").status_code, 403)
        self.assertEqual(self.subir(b"x").status_code, 403)


# ===========================================================================
class TestGananciaReal(ConCatalogo):
    """P-015: "Hacer la union de los productos con los ingredientes para calcular la ganancia real" · responsable:
    "si la hamburguesa […] se le agrega un ingrediente y ese ingrediente tiene que tener un costo […] se le suma una
    carne […] no puede perderse"."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.subir(self.completar(self.hojas())).status_code, 200)

    def pedido(self, items, **extra):
        cuerpo = {"phone": "099 111 222", "address": "Brasil 1234", "courier_id": self.juan,
                  "payment_method": "cash", "items": items, "adjustments": []}
        cuerpo.update(extra)
        r = self.c.post("/api/orders", json=cuerpo)
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()["id"]

    def test_el_extra_suma_su_precio_y_su_costo_y_el_sin_resta(self):
        # Clásica + Extra carne, sin cebolla: cobra $300 + $90
        self.pedido([{"product_id": self.prod["Clásica"], "qty": 1, "added_ingredient_ids": [self.ing["Extra carne"]],
                      "removed_ingredient_ids": [self.ing["Cebolla"]]}])
        g = self.c.get("/api/reportes").get_json()["ganancia_productos"]
        f = {x["nombre"]: x for x in g["filas"]}["Clásica"]
        # costo real: $1.228 − cebolla $8 + una hamburguesa $1.140 = $2.360
        self.assertEqual((f["cobrado_cents"], f["extras_cents"]), (39000, 9000))
        self.assertEqual(f["ganancia_cents"], 39000 - 236000)
        self.assertEqual(f["deja_cents"], 30000 - 122800)  # el «deja c/u» sigue siendo el del producto solo
        # El stock usa la misma cuenta: 2 hamburguesas (0,24 kg) y nada de cebolla
        ins = {i["nombre"]: i for i in self.c.get("/api/insumos").get_json()["insumos"]}
        self.assertEqual(ins["Carne"]["comprometido"], 0.24)
        self.assertEqual(ins["Cebolla"]["comprometido"], 0)

    def test_un_extra_sin_costo_no_se_cuenta_y_se_avisa(self):
        conn = self.db()
        cur = conn.execute("INSERT INTO ingredients(name, extra_price_cents, active) VALUES('Palta', 3200, 1);")
        palta = cur.lastrowid
        conn.commit()
        conn.close()
        self.pedido([{"product_id": self.prod["Doble"], "qty": 2, "added_ingredient_ids": [palta]}])
        g = self.c.get("/api/reportes").get_json()["ganancia_productos"]
        f = {x["nombre"]: x for x in g["filas"]}["Doble"]
        self.assertEqual((f["cobrado_cents"], f["ganancia_cents"]), (90000, 90000 - 2 * 239000))
        self.assertEqual(g["extras_sin_costo"], [{"nombre": "Palta", "veces": 2}])

    def test_la_promo_cuenta_lo_que_se_le_agrego(self):
        self.pedido([{"product_id": self.prod["Clásica"], "qty": 1, "promo_group": "G1", "promo_id": self.promo,
                      "added_ingredient_ids": [self.ing["Extra cheddar"]]},
                     {"product_id": self.prod["Doble"], "qty": 1, "promo_group": "G1", "promo_id": self.promo}])
        f = {x["nombre"]: x for x in self.c.get("/api/reportes").get_json()["ganancia_productos"]["filas"]}
        p = f["Promo pareja"]
        self.assertEqual((p["vendidas"], p["cobrado_cents"]), (1, 75000))
        self.assertEqual(p["ganancia_cents"], 75000 - (122800 + 239000 + 3000))


# ===========================================================================
class TestCarneEnGramos(ConCatalogo):
    """P-016: la carne entra por kg y cada hamburguesa descuenta 120 g exactos."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.subir(self.completar(self.hojas())).status_code, 200)
        conn = self.db()
        self.carne = conn.execute("SELECT id FROM insumos WHERE nombre='Carne';").fetchone()[0]
        conn.close()

    def test_entra_por_kg_y_cada_hamburguesa_descuenta_120_g(self):
        r = self.c.post(f"/api/insumos/{self.carne}/movimiento", json={"tipo": "entrada", "cantidad": "10"})
        self.assertEqual(r.get_json()["stock"], 10)
        cuerpo = {"phone": "1", "address": "Brasil 1", "courier_id": self.juan, "payment_method": "cash",
                  "adjustments": [], "items": [{"product_id": self.prod["Doble"], "qty": 3},
                                               {"product_id": self.prod["Clásica"], "qty": 1}]}
        self.assertEqual(self.c.post("/api/orders", json=cuerpo).status_code, 200)
        self.assertEqual(self.c.post("/api/cierre", json={}).status_code, 200)
        carne = {i["id"]: i for i in self.c.get("/api/insumos").get_json()["insumos"]}[self.carne]
        self.assertEqual(carne["stock"], 9.16)               # 7 hamburguesas × 120 g = 840 g
        self.assertEqual((carne["receta_unidad"], carne["hamburguesas"]), ("hamburguesas", 76))

    def test_la_receta_se_escribe_en_hamburguesas(self):
        r = self.c.put(f"/api/recetas/producto/{self.prod['Papas']}",
                       json={"en_unidad_receta": True, "receta": [{"insumo_id": self.carne, "cantidad": 3}]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.recetas()["Papas"], {"Carne": 0.36})
        p = {x["id"]: x for x in self.c.get("/api/recetas").get_json()["productos"]}[self.prod["Papas"]]
        self.assertEqual((p["receta"][0]["cantidad_receta"], p["receta"][0]["receta_unidad"]), (3, "hamburguesas"))

    def test_cambiar_el_peso_mantiene_las_hamburguesas_y_pasar_a_gramos_no_cambia_lo_que_hay(self):
        self.c.post(f"/api/insumos/{self.carne}/movimiento", json={"tipo": "entrada", "cantidad": "5"})
        base = {"nombre": "Carne", "unidad": "kg", "stock_minimo": 0, "costo_unit": "9500", "activo": True}
        self.assertEqual(self.c.put(f"/api/insumos/{self.carne}", json={**base, "porcion_g": 150}).status_code, 200)
        self.assertEqual(self.recetas()["Doble"]["Carne"], 0.3)   # sigue llevando 2 hamburguesas, ahora de 150 g
        r = self.c.put(f"/api/insumos/{self.carne}", json={**base, "unidad": "g", "porcion_g": 150})
        self.assertEqual(r.status_code, 200)
        i = {x["id"]: x for x in self.c.get("/api/insumos").get_json()["insumos"]}[self.carne]
        self.assertEqual((i["unidad"], i["stock"], i["costo_unit_cents"]), ("g", 5000, 950))
        self.assertEqual(self.recetas()["Doble"]["Carne"], 300)


# ===========================================================================
class TestLiquidacionDetalle(ConCatalogo):
    """P-018: "Poder desglosar total del efectivo a rendir y lo facturado para asegurar que funcione […] que abra
    un modal de detalle de donde viene toda ese dinero"."""

    def test_el_detalle_suma_lo_mismo_que_la_pantalla(self):
        def pedido(medio, estado, courier=True, envio=0):
            cuerpo = {"phone": "1", "address": "Brasil 1", "courier_id": self.juan if courier else None,
                      "payment_method": medio, "items": [{"product_id": self.prod["Clásica"], "qty": 1}],
                      "adjustments": [{"label": "Envío", "amount_cents": envio, "kind": "delivery"}] if envio else []}
            oid = self.c.post("/api/orders", json=cuerpo).get_json()["id"]
            self.c.post(f"/api/orders/{oid}/status", json={"status": estado})
            return oid

        a = pedido("cash", "done", envio=10000)
        b = pedido("transfer", "done")
        c = pedido("cash", "way")
        d = pedido("cash", "done", courier=False)
        l = self.c.get("/api/liquidation").get_json()
        r = self.c.get("/api/liquidation/detalle")
        self.assertEqual(r.status_code, 200)
        det = r.get_json()
        p = {x["id"]: x for x in det["pedidos"]}
        self.assertEqual((p[a]["cuenta"], p[a]["rinde_cents"], p[a]["envio_cents"]), ("rinde", 30000, 10000))
        self.assertEqual((p[b]["cuenta"], p[c]["cuenta"], p[d]["cuenta"]), ("otro_medio", "sin_entregar", "rinde"))
        # efectivo a rendir = lo que rinde cada pedido + el cambio de cada repartidor
        self.assertEqual(sum(x["rinde_cents"] for x in det["pedidos"]) + det["cash_float_total_cents"],
                         l["cash_to_render_cents"])
        self.assertEqual(det["cash_to_render_cents"], l["cash_to_render_cents"])
        self.assertEqual(l["cash_to_render_cents"], 30000 + 30000 + 150000)
        # facturado = todos los pedidos de la jornada, entregados o no
        self.assertEqual(sum(x["total_cents"] for x in det["pedidos"]), l["total_cents"])
        self.assertEqual(det["facturado_cents"], 40000 + 30000 * 3)
        # la caja (tiene liquidación) también lo ve
        self.c.post("/api/logout")
        self.c.post("/api/login", json={"username": "caja", "password": ""})
        self.assertEqual(self.c.get("/api/liquidation/detalle").status_code, 200)


# ===========================================================================
class TestPromosAparte(ConCatalogo):
    """P-017: "Promos en las estadisticas de venta que aparezcan en un card aparte"."""

    def test_las_promos_no_se_mezclan_con_los_productos(self):
        cuerpo = {"phone": "1", "address": "Brasil 1", "courier_id": self.juan, "payment_method": "cash",
                  "adjustments": [], "items": [
                      {"product_id": self.prod["Doble"], "qty": 2},
                      {"product_id": self.prod["Clásica"], "qty": 1, "promo_group": "G1", "promo_id": self.promo},
                      {"product_id": self.prod["Doble"], "qty": 1, "promo_group": "G1", "promo_id": self.promo}]}
        self.assertEqual(self.c.post("/api/orders", json=cuerpo).status_code, 200)
        s = self.c.get("/api/reportes").get_json()["resumen"]
        self.assertEqual([(x["nombre"], x["unidades"]) for x in s["top_productos"]], [("Doble", 2)])
        self.assertEqual([(x["nombre"], x["unidades"], x["total_cents"]) for x in s["top_promos"]],
                         [("Promo pareja", 1, 70000)])
        d = self.c.get("/api/dashboard").get_json()
        self.assertEqual([x["nombre"] for x in d["top_promos"]], ["Promo pareja"])
        self.assertTrue(all(not x["promo"] for x in d["top_productos"]))
        x = self.c.get("/api/exportar.xlsx?tipo=ventas")
        with zipfile.ZipFile(io.BytesIO(x.data)) as z:
            self.assertIn('name="Promos"', z.read("xl/workbook.xml").decode("utf-8"))
        x.close()


if __name__ == "__main__":
    unittest.main()
