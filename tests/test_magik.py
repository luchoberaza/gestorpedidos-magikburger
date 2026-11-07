"""Pruebas de Magik. Corren siempre contra bases temporales: nunca tocan datos reales.

Uso:  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import io
import json
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta
from xml.dom import minidom

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from magik import create_app  # noqa: E402
from magik.db import VERSION_ACTUAL, conectar  # noqa: E402
from magik.dominio import liquidacion as liq  # noqa: E402
from magik.dominio.pedidos import es_rotulo_de_envio, pesos_a_centavos  # noqa: E402


class Base(unittest.TestCase):
    semilla = None  # ruta de una base vieja para copiar, o None = base nueva

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="magik-test-")
        self.ruta = os.path.join(self.dir, "datos", "magik.db")
        os.makedirs(os.path.dirname(self.ruta))
        if self.semilla:
            shutil.copy(self.semilla, self.ruta)
        os.environ["MAGIK_DB_PATH"] = self.ruta
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.c = self.app.test_client()

    def tearDown(self):
        os.environ.pop("MAGIK_DB_PATH", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    # --- ayudas ---
    def db(self):
        return conectar(self.ruta)

    def admin(self):
        r = self.c.post("/api/login", json={"username": "admin", "password": "blamewav"})
        self.assertEqual(r.status_code, 200, r.get_json())

    def catalogo(self):
        conn = self.db()
        cur = conn.cursor()
        cur.execute("INSERT INTO products(name, price_cents, active) VALUES('Clásica', 30000, 1);")
        self.p1 = cur.lastrowid
        cur.execute("INSERT INTO products(name, price_cents, active) VALUES('Doble', 45000, 1);")
        self.p2 = cur.lastrowid
        cur.execute("INSERT INTO ingredients(name, extra_price_cents, active) VALUES('Cheddar', 5000, 1);")
        self.i_ched = cur.lastrowid
        cur.execute("INSERT INTO ingredients(name, extra_price_cents, active) VALUES('Cebolla', 0, 1);")
        self.i_ceb = cur.lastrowid
        cur.execute("INSERT INTO product_base_ingredients VALUES(?, ?);", (self.p1, self.i_ceb))
        cur.execute("INSERT INTO couriers(name, active, cash_float_cents) VALUES('Juan', 1, 150000);")
        self.juan = cur.lastrowid
        cur.execute("INSERT INTO promos(name, price_cents, active) VALUES('Promo 2', 60000, 1);")
        self.promo = cur.lastrowid
        cur.execute("INSERT INTO promo_components(promo_id, product_id, sort_order) VALUES(?,?,0),(?,?,1);",
                    (self.promo, self.p1, self.promo, self.p2))
        conn.commit()
        conn.close()

    def pedido_api(self, **extra):
        cuerpo = {"phone": "099 111 222", "address": "Brasil 1234", "courier_id": self.juan,
                  "payment_method": "cash", "items": [{"product_id": self.p1, "qty": 1}], "adjustments": []}
        cuerpo.update(extra)
        return self.c.post("/api/orders", json=cuerpo)


# ===========================================================================
class TestMigracion(Base):
    semilla = os.path.join(RAIZ, "magikburger.db")

    def test_base_vieja_se_migra_sin_perder_nada(self):
        conn = self.db()
        self.assertEqual(conn.execute("PRAGMA user_version;").fetchone()[0], VERSION_ACTUAL)
        viejo = sqlite3.connect(self.semilla)
        for t in ("orders", "products", "ingredients", "couriers"):
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {t};").fetchone()[0],
                             viejo.execute(f"SELECT COUNT(*) FROM {t};").fetchone()[0], t)
        viejo.close()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM metodos_pago;").fetchone()[0], 2)
        respaldos = os.listdir(os.path.join(self.dir, "datos", "respaldos"))
        self.assertTrue(any(f.startswith("pre-migracion-v0") for f in respaldos), respaldos)
        conn.close()

    def test_admin_entra_con_la_clave_de_siempre_y_la_caja_sin_clave(self):
        r = self.c.get("/api/sesion").get_json()
        self.assertEqual(r["usuario"]["username"], "caja")
        self.assertTrue(r["usuario"]["acceso_libre"])
        self.assertEqual(self.c.post("/api/login", json={"username": "admin", "password": "x"}).status_code, 401)
        self.admin()
        ses = self.c.get("/api/sesion").get_json()
        self.assertEqual(ses["usuario"]["role"], "admin")
        # No se le pide cambiar la contraseña (pedido del responsable, 6-nov).
        self.assertNotIn("clave_insegura", ses)
        # Si quiere cambiarla, puede hacerlo desde su menú.
        r = self.c.post("/api/mi-clave", json={"actual": "blamewav", "nueva": "otra-clave"})
        self.assertEqual(r.status_code, 200)
        self.c.post("/api/logout")
        self.assertEqual(self.c.post("/api/login", json={"username": "admin", "password": "blamewav"}).status_code, 401)
        self.assertEqual(self.c.post("/api/login", json={"username": "admin", "password": "otra-clave"}).status_code, 200)

    def test_migrar_dos_veces_no_rompe(self):
        create_app()
        conn = self.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM users;").fetchone()[0], 2)
        conn.close()


# ===========================================================================
class TestLiquidacion(Base):
    """El efectivo que rinde cada repartidor (los escenarios reales del cliente)."""

    def _pedido(self, conn, cid, total, pm, envio=0, status="done", fecha=None, kind="delivery", label="Envío"):
        cur = conn.execute(
            "INSERT INTO orders(created_at, status, phone, address, courier_id, payment_method, total_cents) "
            "VALUES(?,?,?,?,?,?,?);", (fecha or f"{self.hoy}T20:00:00", status, "099", "Dir", cid, pm, total))
        if envio:
            conn.execute("INSERT INTO order_adjustments(order_id, label, amount_cents, sort_order, kind) "
                         "VALUES(?,?,?,0,?);", (cur.lastrowid, label, envio, kind))

    def setUp(self):
        super().setUp()
        conn = self.db()
        self.hoy = liq.jornada_actual(conn)
        for i, n in ((1, "Juan"), (2, "Mati"), (3, "Ana")):
            conn.execute("INSERT INTO couriers(id, name, active, cash_float_cents) VALUES(?,?,1,150000);", (i, n))
        self._pedido(conn, 1, 200000, "cash", envio=30000)
        self._pedido(conn, 1, 300000, "transfer", envio=30000)
        self._pedido(conn, 1, 250000, "cash", envio=30000, status="kitchen")
        self._pedido(conn, 2, 150000, "cash", envio=20000, kind="", label="Flete")  # sin kind: no es envío
        self._pedido(conn, None, 100000, "cash")
        self._pedido(conn, 3, 400000, "transfer")
        self._pedido(conn, 1, 68400, "cash", fecha="2020-01-01T20:00:00")
        conn.commit()
        conn.close()
        self.admin()

    def test_pantalla_y_calculo_coinciden_con_la_plata_real(self):
        liq_api = self.c.get("/api/liquidation").get_json()
        por = {c["courier_name"]: c["cash_cents"] for c in liq_api["couriers"]}
        esperado = {"Juan": 200000 - 30000 + 150000, "Mati": 150000 + 150000, "Ana": 150000,
                    "Sin repartidor": 100000}
        self.assertEqual(por, esperado)
        self.assertEqual(liq_api["cash_to_render_cents"], sum(esperado.values()))
        self.assertEqual(liq_api["pending_cents"], 250000)
        self.assertEqual(liq_api["carryover_count"], 1)
        self.assertEqual(liq_api["carryover_cents"], 68400)
        conn = self.db()
        directo = liq.calcular(conn, self.hoy)
        conn.close()
        self.assertEqual(directo["cash_to_render_cents"], liq_api["cash_to_render_cents"])

    def test_pedido_de_madrugada_cuenta_para_la_noche_anterior(self):
        conn = self.db()
        manana = (datetime.fromisoformat(self.hoy) + timedelta(days=1)).date().isoformat()
        self.assertEqual(liq.jornada_de(datetime.fromisoformat(f"{manana}T01:30:00"), 6), self.hoy)
        self.assertEqual(liq.jornada_de(datetime.fromisoformat(f"{manana}T06:00:00"), 6), manana)
        conn.close()

    def test_medio_de_pago_configurable_que_no_es_efectivo_no_se_rinde(self):
        self.c.post("/api/metodos-pago", json={"name": "Mercado Pago", "is_cash": False})
        conn = self.db()
        conn.execute("DELETE FROM orders;")
        self._pedido(conn, 1, 50000, "mercadopago")
        conn.commit()
        r = liq.calcular(conn, self.hoy)
        conn.close()
        self.assertEqual(r["couriers"][0]["to_render_cents"], 150000)  # solo el cambio
        self.assertEqual(r["transfer_cents"], 50000)

    def test_textos_de_compatibilidad(self):
        self.assertTrue(liq.es_transferencia_por_texto("mp"))
        self.assertFalse(liq.es_transferencia_por_texto("compra en efectivo"))
        self.assertTrue(es_rotulo_de_envio("Envío"))
        self.assertFalse(es_rotulo_de_envio("Delivery"))


class TestLiquidacionMasiva(Base):
    """Miles de jornadas al azar contra un modelo independiente: el bolsillo del repartidor."""

    def test_jornadas_al_azar(self):
        conn = self.db()
        hoy = liq.jornada_actual(conn)
        cambios = {}
        for i in range(1, 5):
            cambios[i] = random.Random(i).choice([100000, 150000, 200000, 0])
            conn.execute("INSERT INTO couriers(id, name, active, cash_float_cents) VALUES(?,?,1,?);",
                         (i, f"R{i}", cambios[i]))
        conn.commit()
        for n in range(800):
            rnd = random.Random(n)
            conn.execute("DELETE FROM orders;")
            conn.execute("DELETE FROM order_adjustments;")
            esperado, salieron, local = {}, set(), False
            pedidos = []
            for _ in range(rnd.randint(0, 12)):
                cid = rnd.choice([1, 2, 3, 4, None, None])
                st = rnd.choice(["new", "kitchen", "way", "done", "done", "done"])
                pm = rnd.choice(["cash", "cash", "cash", "transfer"])
                total = rnd.randrange(50000, 900000, 5000)
                envio, kind, label = 0, "", ""
                if rnd.random() < 0.55:
                    envio = rnd.randrange(10000, 60000, 5000)
                    modo = rnd.choice(["kind", "kind", "viejo", "raro"])
                    kind = "delivery" if modo == "kind" else ""
                    label = {"kind": "Delivery", "viejo": "Envío", "raro": "Recargo"}[modo]
                vieja = rnd.random() < 0.12
                fecha = "2020-06-15T20:00:00" if vieja else f"{hoy}T20:00:00"
                cur = conn.execute("INSERT INTO orders(created_at, status, phone, address, courier_id, payment_method, "
                                   "total_cents) VALUES(?,?,?,?,?,?,?);", (fecha, st, "0", "D", cid, pm, total))
                if envio:
                    conn.execute("INSERT INTO order_adjustments(order_id, label, amount_cents, sort_order, kind) "
                                 "VALUES(?,?,?,0,?);", (cur.lastrowid, label, envio, kind))
                pedidos.append((cid, st, pm, total, envio, kind, label, vieja))
            conn.commit()
            # modelo del bolsillo
            for cid, st, pm, total, envio, kind, label, vieja in pedidos:
                if vieja:
                    continue
                if cid is None:
                    local = True
                else:
                    salieron.add(cid)
            bolsillo = {cid: cambios[cid] for cid in salieron}
            if local:
                bolsillo[None] = 0
            for cid, st, pm, total, envio, kind, label, vieja in pedidos:
                if vieja or st != "done" or pm == "transfer":
                    continue
                bolsillo[cid] += total
                if envio and (kind == "delivery" or es_rotulo_de_envio(label)):
                    bolsillo[cid] -= envio
            r = liq.calcular(conn, hoy)
            obtenido = {c["courier_id"]: c["to_render_cents"] for c in r["couriers"]}
            self.assertEqual(obtenido, bolsillo, f"jornada {n}")
            self.assertEqual(r["cash_to_render_cents"], sum(bolsillo.values()))
        conn.close()


# ===========================================================================
class TestPedidos(Base):
    def setUp(self):
        super().setUp()
        self.catalogo()

    def test_total_con_promo_extras_y_ajustes(self):
        items = [{"product_id": self.p1, "qty": 2, "added_ingredient_ids": [self.i_ched]},
                 {"product_id": self.p1, "qty": 1, "promo_group": "G1", "promo_id": self.promo},
                 {"product_id": self.p2, "qty": 1, "promo_group": "G1", "promo_id": self.promo,
                  "added_ingredient_ids": [self.i_ched]}]
        ajustes = [{"label": "Envío", "amount_cents": 10000, "kind": "delivery"},
                   {"label": "Descuento", "amount_cents": -5000}]
        esperado = 2 * (30000 + 5000) + 60000 + 5000 + 10000 - 5000
        r = self.pedido_api(items=items, adjustments=ajustes, client_total_cents=esperado)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["total_cents"], esperado)
        t = self.c.get(f"/orders/{r.get_json()['id']}/ticket")
        self.assertEqual(t.status_code, 200)
        self.assertIn("Promo 2", t.get_data(as_text=True))

    def test_ticket_termico_sin_textos_fijos(self):
        from magik.ticket import bytes_escpos, bytes_escpos_texto, contexto
        self.admin()
        self.c.post("/api/config", json={"ticket_encabezado": "La Burgue", "ticket_pie": "Vuelva pronto"})
        oid = self.pedido_api(notes="Sin sal", payment_method="transfer").get_json()["id"]
        conn = self.db()
        ctx = contexto(conn, oid)
        conn.close()
        self.assertTrue(ctx["order"]["ocultar_precios"])
        texto = bytes_escpos_texto(ctx)
        self.assertIn(b"La Burgue", texto)
        self.assertIn(b"Vuelva pronto", texto)
        self.assertIn(b"Nota: Sin sal", texto)
        self.assertIn(b"TRANSFERIDO", texto)
        self.assertNotIn(b"Magik", texto)
        datos, modo = bytes_escpos(ctx)
        self.assertTrue(datos.startswith(b"@"))
        self.assertIn(modo, ("poppins", "texto"))
        html = self.c.get(f"/orders/{oid}/ticket").get_data(as_text=True)
        self.assertIn("La Burgue", html)
        self.assertIn("Vuelva pronto", html)

    def test_ticket_dice_unidades_en_vez_de_x(self):
        # Pedido del local (P-011): "cuando vendes 2 unidades de un producto que no es un
        # combo […] que en vez de xalgo diga x unidades".
        from magik.ticket import bytes_escpos_texto, contexto, unidades
        self.assertEqual(unidades(1), "1 unidad")
        self.assertEqual(unidades(2), "2 unidades")
        items = [{"product_id": self.p1, "qty": 2}, {"product_id": self.p2, "qty": 1}]
        r = self.pedido_api(items=items)
        self.assertEqual(r.status_code, 200, r.get_json())
        oid = r.get_json()["id"]
        conn = self.db()
        ctx = contexto(conn, oid)
        conn.close()
        # Sin los códigos de negrita de la térmica (ESC E 0/1), solo el texto de cada línea.
        lineas = [x.replace(b"\x1bE\x00", b"").replace(b"\x1bE\x01", b"") for x in bytes_escpos_texto(ctx).split(b"\n")]
        self.assertIn(b"2 unidades", lineas)
        self.assertIn(b"1 unidad", lineas)
        self.assertNotIn(b"x2", lineas)
        self.assertNotIn(b"x1", lineas)
        html = self.c.get(f"/orders/{oid}/ticket").get_data(as_text=True)
        self.assertIn(">2 unidades<", html)
        self.assertIn(">1 unidad<", html)
        self.assertNotIn('class="it-qty">x', html)

    def test_total_distinto_al_de_la_pantalla_no_se_guarda(self):
        r = self.pedido_api(client_total_cents=1)
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.get_json()["stale"])
        conn = self.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders;").fetchone()[0], 0)
        conn.close()

    def test_producto_inexistente_rechaza_todo(self):
        r = self.pedido_api(items=[{"product_id": 9999}])
        self.assertEqual(r.status_code, 409)

    def test_faltan_datos(self):
        self.assertEqual(self.pedido_api(address="").status_code, 400)
        self.assertEqual(self.pedido_api(items=[]).status_code, 400)

    def test_editar_entregado_pide_confirmacion_y_conserva_el_envio(self):
        oid = self.pedido_api(adjustments=[{"label": "Delivery", "amount_cents": 10000, "kind": "delivery"}]).get_json()["id"]
        self.c.post(f"/api/orders/{oid}/status", json={"status": "done"})
        leido = self.c.get(f"/api/orders/{oid}").get_json()
        self.assertEqual(leido["adjustments"][0]["kind"], "delivery")
        cuerpo = {"phone": "1", "address": "Otra 1", "courier_id": self.juan, "payment_method": "cash",
                  "items": leido["items"], "adjustments": leido["adjustments"]}
        self.assertEqual(self.c.post(f"/api/orders/{oid}/update", json=cuerpo).status_code, 409)
        cuerpo["confirm_delivered"] = True
        self.assertEqual(self.c.post(f"/api/orders/{oid}/update", json=cuerpo).status_code, 200)

    def test_sugerencia_de_direccion_por_telefono(self):
        self.pedido_api(phone="099 123 456", address="Rivera 2233")
        r = self.c.get("/api/clientes?tel=099123456").get_json()
        self.assertEqual(r["direcciones"], ["Rivera 2233"])


# ===========================================================================
class TestMejoresClientes(Base):
    """Pedido del responsable (6-nov): "una analítica del cliente que más pide en el mes […]
    lo que los verifica son las direcciones […] 6 meses para atrás […] los 5 que más compraron"."""

    def setUp(self):
        super().setUp()
        self.catalogo()
        self.admin()
        self.periodos = self.c.get("/api/clientes/mejores").get_json()["periodos"]

    def _fecha(self, meses_atras):
        if meses_atras < len(self.periodos) - 1:
            return self.periodos[meses_atras]["desde"][:8] + "15T13:00:00"
        # Un mes más viejo que el último que se puede elegir.
        d = datetime.fromisoformat(self.periodos[-2]["desde"]) - timedelta(days=1)
        return d.strftime("%Y-%m-15T13:00:00")

    def _pedido(self, direccion, productos, meses_atras=0, envio=0, tel="099 111 222"):
        ajustes = [{"label": "Envío", "amount_cents": envio, "kind": "delivery"}] if envio else []
        r = self.pedido_api(address=direccion, phone=tel, adjustments=ajustes,
                            items=[{"product_id": p, "qty": 1} for p in productos])
        self.assertEqual(r.status_code, 200, r.get_json())
        conn = self.db()
        conn.execute("UPDATE orders SET created_at=? WHERE id=?;", (self._fecha(meses_atras), r.get_json()["id"]))
        conn.commit()
        conn.close()

    def _ver(self, **args):
        r = self.c.get("/api/clientes/mejores?" + "&".join(f"{k}={v}" for k, v in args.items()))
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()

    def _cargar(self):
        # Mes actual: la misma dirección escrita de tres formas es un solo cliente.
        self._pedido("Brasil 1234", [self.p1], envio=10000)
        self._pedido("BRASIL  1234.", [self.p1], tel="098 765 432")
        self._pedido("brasil 1234", [self.p1])
        self._pedido("Rivera 500", [self.p2, self.p2])
        self._pedido("Rivera 500", [self.p2, self.p2])
        self._pedido("Colonia 800", [self.p1])
        self._pedido("Brasil 1234 apto 2", [self.p1])  # otro cliente: otro apartamento
        # Mes anterior y uno fuera de los meses que se pueden elegir.
        for _ in range(4):
            self._pedido("Colonia 800", [self.p1], meses_atras=1)
        self._pedido("Lejana 1", [self.p2] * 5, meses_atras=99)

    def test_el_mes_actual_agrupa_por_direccion_y_no_cuenta_el_envio(self):
        self._cargar()
        r = self._ver()
        self.assertEqual(r["periodo"]["id"], self.periodos[0]["id"])
        self.assertEqual(r["clientes_distintos"], 4)
        primero, segundo = r["clientes"][0], r["clientes"][1]
        self.assertEqual(primero["direccion"], "brasil 1234")  # cómo se escribió la última vez
        self.assertEqual(primero["pedidos"], 3)
        self.assertEqual(primero["total_cents"], 3 * 30000)  # el envío es del repartidor
        self.assertEqual(sorted(primero["telefonos"]), ["098 765 432", "099 111 222"])
        self.assertEqual(primero["favorito"], {"nombre": "Clásica", "unidades": 3})
        self.assertEqual(segundo["direccion"], "Rivera 500")
        self.assertEqual(segundo["pedidos"], 2)
        self.assertEqual(segundo["ticket_promedio_cents"], 90000)
        self.assertEqual(segundo["favorito"], {"nombre": "Doble", "unidades": 4})
        self.assertNotIn("Lejana 1", [c["direccion"] for c in r["clientes"]])

    def test_muestra_la_direccion_como_se_escribe_mas_seguido(self):
        self._pedido("Rivera 500", [self.p1])
        self._pedido("Rivera 500", [self.p2])
        self._pedido("Rivera  500", [self.p1])
        self._pedido("RIVERA 500", [self.p1])  # la última vez, en mayúsculas
        c = self._ver()["clientes"][0]
        self.assertEqual((c["direccion"], c["pedidos"]), ("Rivera 500", 4))
        self.assertEqual(c["ticket_promedio_cents"], 33800)  # $ 1.350 / 4 = $ 337,50 → $ 338

    def test_se_puede_ordenar_por_lo_que_gastaron(self):
        self._cargar()
        r = self._ver(orden="plata")
        self.assertEqual([c["direccion"] for c in r["clientes"][:2]], ["Rivera 500", "brasil 1234"])

    def test_meses_anteriores_y_los_seis_meses_juntos(self):
        self._cargar()
        self.assertEqual(len(self.periodos), 7)  # el mes actual, los 5 anteriores y "los 6 juntos"
        anterior = self._ver(periodo=self.periodos[1]["id"])
        self.assertEqual(anterior["clientes"][0]["direccion"], "Colonia 800")
        self.assertEqual(anterior["clientes"][0]["pedidos"], 4)
        juntos = self._ver(periodo="todos")
        self.assertEqual([(c["direccion"], c["pedidos"]) for c in juntos["clientes"][:2]],
                         [("Colonia 800", 5), ("brasil 1234", 3)])
        self.assertNotIn("Lejana 1", [c["direccion"] for c in juntos["clientes"]])

    def test_muestra_los_cinco_primeros_y_exporta_todos(self):
        for i in range(7):
            for _ in range(i + 1):
                self._pedido(f"Calle {i}", [self.p1])
        r = self._ver()
        self.assertEqual([c["puesto"] for c in r["clientes"]], [1, 2, 3, 4, 5])
        self.assertEqual(r["clientes"][0]["direccion"], "Calle 6")
        x = self.c.get("/api/clientes/mejores.xlsx")
        self.assertEqual(x.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(x.data)) as z:
            hoja = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        minidom.parseString(hoja)
        for i in range(7):
            self.assertIn(f"Calle {i}", hoja)

    def test_periodo_invalido_y_sin_permiso(self):
        self.assertEqual(self.c.get("/api/clientes/mejores?periodo=2001-01").status_code, 400)
        caja = self.app.test_client()  # entra como Caja, que no tiene Reportes
        self.assertEqual(caja.get("/api/clientes/mejores").status_code, 403)
        self.assertEqual(caja.get("/api/clientes/mejores.xlsx").status_code, 403)

    def test_direcciones_iguales_sin_importar_tildes_ni_puntos(self):
        from magik.dominio.clientes import clave_direccion
        self.assertEqual(clave_direccion("Av. San Martín  N° 55"), clave_direccion("av san martin n 55"))
        self.assertNotEqual(clave_direccion("Brasil 1234"), clave_direccion("Brasil 1234 apto 2"))
        self.assertEqual(clave_direccion("   "), "")


# ===========================================================================
class TestCierreYStock(Base):
    def setUp(self):
        super().setUp()
        self.catalogo()
        self.admin()
        conn = self.db()
        cur = conn.cursor()
        cur.execute("INSERT INTO insumos(nombre, unidad, stock, stock_minimo) VALUES('Pan', 'unidades', 100, 10);")
        self.pan = cur.lastrowid
        cur.execute("INSERT INTO insumos(nombre, unidad, stock, stock_minimo) VALUES('Cheddar', 'fetas', 100, 10);")
        self.feta = cur.lastrowid
        cur.execute("INSERT INTO insumos(nombre, unidad, stock, stock_minimo) VALUES('Cebolla', 'g', 1000, 0);")
        self.cebolla = cur.lastrowid
        conn.commit()
        conn.close()
        self.c.put(f"/api/recetas/producto/{self.p1}", json={"receta": [
            {"insumo_id": self.pan, "cantidad": 1}, {"insumo_id": self.cebolla, "cantidad": 20}]})
        self.c.put(f"/api/recetas/ingrediente/{self.i_ched}", json={"insumo_id": self.feta, "cantidad": 2})
        self.c.put(f"/api/recetas/ingrediente/{self.i_ceb}", json={"insumo_id": self.cebolla, "cantidad": 20})

    def test_cierre_guarda_historial_descuenta_stock_y_respalda(self):
        # 3 clásicas con cheddar extra x1 y una sin cebolla
        self.pedido_api(items=[{"product_id": self.p1, "qty": 3, "added_ingredient_ids": [self.i_ched]}],
                        adjustments=[{"label": "Envío", "amount_cents": 10000, "kind": "delivery"}])
        oid2 = self.pedido_api(items=[{"product_id": self.p1, "removed_ingredient_ids": [self.i_ceb]}]).get_json()["id"]
        self.c.post(f"/api/orders/{oid2}/status", json={"status": "done"})
        ins = {i["id"]: i for i in self.c.get("/api/insumos").get_json()["insumos"]}
        self.assertEqual(ins[self.pan]["comprometido"], 4)
        self.assertEqual(ins[self.feta]["comprometido"], 6)
        self.assertEqual(ins[self.cebolla]["comprometido"], 60)

        r = self.c.post("/api/cierre", json={"include_adjustments": True})
        self.assertEqual(r.status_code, 200, r.get_json())
        cid = r.get_json()["cierre_id"]
        self.assertEqual(sum(len(v) for k, v in self.c.get("/api/board").get_json().items() if isinstance(v, list)), 0)
        conn = self.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders WHERE cierre_id=?;", (cid,)).fetchone()[0], 2)
        stock = {r2["id"]: r2["stock"] for r2 in conn.execute("SELECT id, stock FROM insumos;")}
        conn.close()
        self.assertEqual(stock[self.pan], 96)
        self.assertEqual(stock[self.feta], 94)
        self.assertEqual(stock[self.cebolla], 940)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "datos", "reportes", r.get_json()["archivo"])))
        self.assertTrue(any(f.startswith("cierre_") for f in os.listdir(os.path.join(self.dir, "datos", "respaldos"))))
        pdf = self.c.get(f"/api/cierres/{cid}/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        pdf.close()
        # el historial sigue en ventas, sin el envío
        v = self.c.get("/api/ventas").get_json()
        self.assertEqual(v["total_pedidos_cents"], 3 * 35000 + 30000)
        self.assertEqual(v["total_envios_cents"], 10000)
        # un pedido cerrado no se puede tocar
        self.assertEqual(self.c.post(f"/api/orders/{oid2}/delete").status_code, 409)

    def test_cierre_sin_pedidos(self):
        self.assertEqual(self.c.post("/api/cierre", json={}).status_code, 400)

    def test_movimientos_y_compra_como_gasto(self):
        r = self.c.post(f"/api/insumos/{self.pan}/movimiento",
                        json={"tipo": "entrada", "cantidad": 50, "registrar_gasto": True, "costo_total": "2.500"})
        self.assertEqual(r.get_json()["stock"], 150)
        g = self.c.get("/api/gastos").get_json()
        self.assertEqual(g["total_cents"], 250000)
        r = self.c.post(f"/api/insumos/{self.pan}/movimiento", json={"tipo": "conteo", "cantidad": 120})
        self.assertEqual(r.get_json()["stock"], 120)
        self.assertEqual(self.c.post(f"/api/insumos/{self.pan}/movimiento", json={"tipo": "salida", "cantidad": 0}).status_code, 400)


# ===========================================================================
class TestPermisos(Base):
    def test_la_caja_no_administra(self):
        for metodo, url in (("post", "/api/cierre"), ("get", "/api/usuarios"), ("get", "/api/config"),
                            ("get", "/api/admin/catalogo"), ("get", "/api/reportes"), ("get", "/api/exportar.pdf")):
            self.assertEqual(getattr(self.c, metodo)(url).status_code, 403, url)
        self.assertEqual(self.c.get("/api/gastos").status_code, 200)

    def test_sin_acceso_libre_hay_que_ingresar(self):
        self.admin()
        self.c.post("/api/config", json={"acceso_libre_usuario_id": ""})
        self.c.post("/api/logout")
        self.assertIsNone(self.c.get("/api/sesion").get_json()["usuario"])
        self.assertEqual(self.c.get("/api/board").status_code, 401)
        r = self.c.post("/api/login", json={"username": "caja", "password": ""})
        self.assertEqual(r.status_code, 401)

    def test_usuario_con_permisos_a_medida(self):
        self.admin()
        r = self.c.post("/api/usuarios", json={"name": "Ana", "username": "ana", "role": "cajero",
                                                "password": "ana1234", "permisos": ["reportes"]})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.c.post("/api/logout")
        self.assertEqual(self.c.post("/api/login", json={"username": "ana", "password": "ana1234"}).status_code, 200)
        self.assertEqual(self.c.get("/api/reportes").status_code, 200)
        self.assertEqual(self.c.get("/api/gastos").status_code, 403)

    def test_no_se_queda_sin_administradores(self):
        self.admin()
        admin_id = self.c.get("/api/sesion").get_json()["usuario"]["id"]
        r = self.c.put(f"/api/usuarios/{admin_id}", json={"name": "A", "username": "admin", "role": "cajero"})
        self.assertEqual(r.status_code, 400)


# ===========================================================================
class TestConfigYFinanzas(Base):
    def setUp(self):
        super().setUp()
        self.admin()

    def test_configuracion_valida(self):
        self.assertEqual(self.c.post("/api/config", json={"ticket_copies": 50}).status_code, 400)
        self.assertEqual(self.c.post("/api/config", json={"negocio_nombre": "La Burgue", "ticket_pie": "¡Gracias!"}).status_code, 200)
        self.assertEqual(self.c.get("/api/sesion").get_json()["negocio"]["nombre"], "La Burgue")

    def test_gastos_ventas_manuales_y_reportes(self):
        hoy = self.c.get("/api/sesion").get_json()["hoy"]
        self.assertEqual(self.c.post("/api/gastos", json={"descripcion": "Luz", "importe": "1.500", "fecha": hoy}).status_code, 200)
        self.assertEqual(self.c.post("/api/gastos", json={"descripcion": "", "importe": "10"}).status_code, 400)
        self.assertEqual(self.c.post("/api/ventas-manuales", json={"concepto": "Evento", "cantidad": 2, "precio_unit": "1000"}).status_code, 200)
        r = self.c.get(f"/api/reportes?desde={hoy}&hasta={hoy}").get_json()["resumen"]
        self.assertEqual(r["gastos_cents"], 150000)
        self.assertEqual(r["ventas_cents"], 200000)
        self.assertEqual(r["balance_cents"], 50000)
        x = self.c.get(f"/api/exportar.xlsx?tipo=completo&desde={hoy}&hasta={hoy}")
        z = zipfile.ZipFile(io.BytesIO(x.data))
        for n in z.namelist():
            minidom.parseString(z.read(n))
        self.assertIn("xl/worksheets/sheet1.xml", z.namelist())
        self.assertTrue(self.c.get(f"/api/exportar.pdf?tipo=completo&desde={hoy}&hasta={hoy}").data.startswith(b"%PDF"))

    def test_pago_fijo_avisa_y_al_pagarlo_queda_como_gasto(self):
        hoy = self.c.get("/api/sesion").get_json()["hoy"]
        dia = int(hoy[8:10])
        rid = self.c.post("/api/recordatorios", json={"servicio": "Alquiler", "dia": dia, "monto": "20000"}).get_json()["id"]
        avisos = self.c.get("/api/notificaciones").get_json()["avisos"]
        self.assertTrue(any(a["titulo"] == "Alquiler" for a in avisos))
        self.c.post(f"/api/recordatorios/{rid}/pagar", json={"registrar_gasto": 1})
        self.assertEqual(self.c.get("/api/gastos").get_json()["total_cents"], 2000000)
        avisos = self.c.get("/api/notificaciones").get_json()["avisos"]
        self.assertFalse(any(a["titulo"] == "Alquiler" and "vence hoy" in a["detalle"] for a in avisos))

    def test_respaldo_y_restauracion(self):
        self.c.post("/api/gastos", json={"descripcion": "Antes", "importe": "100"})
        copia = self.c.get("/api/respaldo/descargar").data
        self.assertTrue(copia.startswith(b"SQLite format 3"))
        self.c.post("/api/gastos", json={"descripcion": "Despues", "importe": "100"})
        r = self.c.post("/api/respaldo/restaurar", data={"archivo": (io.BytesIO(copia), "r.db")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.admin()
        descs = [g["descripcion"] for g in self.c.get("/api/gastos").get_json()["gastos"]]
        self.assertEqual(descs, ["Antes"])

    def test_pesos(self):
        for txt, cents in (("1500", 150000), ("1.500", 150000), ("1.500,50", 150050), ("12,5", 1250),
                           ("$ 200", 20000), ("-200", 20000), ("abc", 0)):
            self.assertEqual(pesos_a_centavos(txt), cents, txt)
        self.assertEqual(pesos_a_centavos("-200", negativo=True), -20000)


# ===========================================================================
class TestImportarCaja(Base):
    def _caja_db(self):
        ruta = os.path.join(self.dir, "caja.db")
        c = sqlite3.connect(ruta)
        c.executescript("""
          CREATE TABLE usuarios (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, nombre TEXT, role TEXT);
          CREATE TABLE ventas (id INTEGER PRIMARY KEY, fecha TEXT, hora TEXT, producto TEXT, cantidad REAL,
            precioUnitario REAL, total REAL, metodoPago TEXT, observaciones TEXT, invProductoId INTEGER);
          CREATE TABLE gastos (id INTEGER PRIMARY KEY, fecha TEXT, categoria TEXT, descripcion TEXT, proveedor TEXT,
            importe REAL, metodoPago TEXT, observaciones TEXT);
          CREATE TABLE inventario (id INTEGER PRIMARY KEY, nombre TEXT UNIQUE, categoria TEXT, unidad TEXT,
            stockActual REAL, stockMinimo REAL, costoUnitario REAL);
          CREATE TABLE movimientos_stock (id INTEGER PRIMARY KEY, fecha TEXT, productoId INTEGER, cantidad REAL, tipo TEXT, motivo TEXT);
          CREATE TABLE recordatorios (id INTEGER PRIMARY KEY, servicio TEXT, dia INTEGER, monto REAL);
          CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT);
          INSERT INTO usuarios VALUES (1,'admin','admin123','Administrador','admin'), (2,'lucia','1234','Lucía','empleado');
          INSERT INTO ventas VALUES (1,'2026-08-01','21:00','Doble Cheddar',2,450.5,901,'Efectivo','',NULL);
          INSERT INTO gastos VALUES (1,'2026-08-01','Carne','Carne picada','Frigorífico',12000,'Transferencia','');
          INSERT INTO gastos VALUES (2,'2026-08-02','Leña','Leña','',800,'Efectivo','');
          INSERT INTO inventario VALUES (1,'Pan','Pan','unidades',40,10,25);
          INSERT INTO movimientos_stock VALUES (1,'2026-08-01',1,-2,'venta','Venta: Doble Cheddar');
          INSERT INTO recordatorios VALUES (1,'Internet',15,1800);
          INSERT INTO config VALUES ('umbralGasto','50000');
        """)
        c.commit()
        c.close()
        with open(ruta, "rb") as f:
            return f.read()

    def test_importa_todo_y_no_duplica(self):
        self.admin()
        datos = self._caja_db()
        subir = lambda: self.c.post("/api/importar/caja", data={"archivo": (io.BytesIO(datos), "caja.db")},
                                    content_type="multipart/form-data")
        r = subir()
        self.assertEqual(r.status_code, 200, r.get_json())
        res = r.get_json()["resultado"]
        self.assertEqual((res["gastos"], res["ventas"], res["insumos"], res["movimientos"], res["recordatorios"],
                          res["usuarios"]), (2, 1, 1, 1, 1, 1))  # 'admin' ya existía
        self.assertEqual(subir().get_json()["resultado"]["gastos"], 0)
        conn = self.db()
        self.assertEqual(conn.execute("SELECT total_cents FROM ventas_manuales;").fetchone()[0], 90100)
        self.assertEqual(conn.execute("SELECT importe_cents FROM gastos WHERE descripcion='Carne picada';").fetchone()[0], 1200000)
        conn.close()
        cats = self.c.get("/api/opciones").get_json()["categorias_gasto"]
        self.assertIn("Leña", cats)
        self.c.post("/api/logout")
        self.assertEqual(self.c.post("/api/login", json={"username": "lucia", "password": "1234"}).status_code, 200)

    def test_rechaza_un_archivo_que_no_es_de_caja(self):
        self.admin()
        r = self.c.post("/api/importar/caja", data={"archivo": (io.BytesIO(b"hola"), "x.db")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
