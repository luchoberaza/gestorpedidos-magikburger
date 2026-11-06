import { useEffect, useRef, useState } from "react";
import { ArrowDownRight, ArrowUpRight, Bike, CalendarClock, Check, Download, FileSpreadsheet, FileText, Pencil, Plus, Printer, Receipt, ShoppingBag, Trash2, TrendingUp, Wallet, Boxes } from "lucide-react";
import { bus, del, descargar, fecha, fechaCorta, fechaHora, get, hoy, imprimirTicket, intentar, numero, periodo, pesos, plata, post, put, puede, qs, textoPeriodo, toast } from "../lib";
import { BarraPeriodo, Barras, Btn, COLORES, Cargando, Delta, Desplegable, Dona, Kpi, Ranking, Tabla, Tabs, Tag, Vacio, Vidrio, confirmar, formulario, useCarga } from "../ui";
import { Encabezado } from "./Operacion";

let cacheOpc = null;
export async function opciones() { if (!cacheOpc) cacheOpc = await get("/api/opciones"); return cacheOpc; }
export const limpiarOpciones = () => { cacheOpc = null; };

/* ---------------- Inicio ---------------- */
export function Inicio({ ir }) {
  const carga = useCarga(() => get("/api/dashboard"), []);
  return <Cargando carga={carga}>{(d) => {
    const dia = d.dia, mes = d.mes;
    const horas = d.pedidos_por_hora.filter((h) => h.pedidos);
    const sel = horas.length ? d.pedidos_por_hora.filter((h) => h.hora >= Math.min(...horas.map((x) => x.hora)) && h.hora <= Math.max(...horas.map((x) => x.hora))) : [];
    return <>
      <Encabezado titulo="Inicio" sub={`Resumen del negocio · ${fecha(d.hoy)}`} />
      <div className="grilla g4">
        <Kpi destacado c="p" icono={TrendingUp} label="Ventas de hoy" valor={plata(dia.ventas_cents)} delta={<Delta actual={dia.ventas_cents} previo={d.ayer.ventas_cents} />} sub={`${dia.pedidos_count} pedido(s)`} />
        <Kpi c="r" icono={Receipt} label="Gastos de hoy" valor={plata(dia.gastos_cents)} sub={`${dia.gastos_count} gasto(s)`} />
        <Kpi c={dia.balance_cents >= 0 ? "g" : "r"} icono={Wallet} label="Ganancia de hoy" valor={plata(dia.balance_cents)} sub="Ventas − gastos" />
        <Kpi c="v" icono={ShoppingBag} label="Ticket promedio" valor={plata(dia.ticket_promedio_cents)} sub="Por pedido, sin envío" />
      </div>
      <div className="grilla g4 mt">
        <Kpi c="v" icono={ArrowUpRight} label="Ventas del mes" valor={plata(mes.ventas_cents)} sub={`${mes.pedidos_count} pedido(s)`} />
        <Kpi c="r" icono={ArrowDownRight} label="Gastos del mes" valor={plata(mes.gastos_cents)} sub={`${mes.gastos_count} gasto(s)`} />
        <Kpi c={mes.balance_cents >= 0 ? "g" : "r"} icono={Wallet} label="Ganancia del mes" valor={plata(mes.balance_cents)} sub="Ventas − gastos" />
        <Kpi c="b" icono={Bike} label="Envíos del mes" valor={plata(mes.envios_cents)} sub="Cobrados para los repartidores" />
      </div>
      <div className="grilla g2 mt">
        <Vidrio titulo="Últimos 14 días" extra={<Tag>Ventas vs gastos</Tag>}><Barras etiquetas={d.serie.map((x) => fechaCorta(x.dia))} series={[{ nombre: "Ventas", color: COLORES[0], valores: d.serie.map((x) => x.ventas) }, { nombre: "Gastos", color: COLORES[1], valores: d.serie.map((x) => x.gastos) }]} /></Vidrio>
        <Vidrio titulo="Gastos del mes por categoría"><Dona items={d.gastos_por_categoria.map((x) => ({ etiqueta: x.categoria, valor: x.total_cents }))} vacio="Sin gastos este mes" /></Vidrio>
      </div>
      <div className="grilla g2 mt">
        <Vidrio titulo="Lo más vendido del mes" extra={puede("reportes") && <Btn v="fant" ch onClick={() => ir("reportes")}>Ver reportes</Btn>}><Ranking items={d.top_productos.map((p) => ({ etiqueta: p.nombre, valor: p.total_cents, extra: `${numero(p.unidades)} u.` }))} /></Vidrio>
        <Vidrio titulo="Pedidos por hora (mes)">{sel.length ? <Barras alto={210} etiquetas={sel.map((h) => `${h.hora}h`)} series={[{ nombre: "Pedidos", color: COLORES[2], valores: sel.map((h) => h.pedidos) }]} fmt={(v) => `${v} pedido(s)`} eje={(v) => String(Math.round(v))} /> : <Vacio titulo="Sin pedidos este mes" />}</Vidrio>
      </div>
      <div className="grilla g2 mt">
        <Vidrio titulo="Pagos fijos próximos" extra={puede("gastos") && <Btn v="fant" ch onClick={() => ir("gastos", { tab: "fijos" })}>Administrar</Btn>}>
          {d.recordatorios.length ? <div className="col s">{d.recordatorios.map((r) => <div key={r.id} className="fila sb"><span><b>{r.servicio}</b> <span className="t3 small">día {r.dia}{r.monto_cents ? ` · ${plata(r.monto_cents)}` : ""}</span></span>
            <Tag c={r.vencido ? "r" : "a"}>{r.vencido ? `Vencido hace ${-r.dias_restantes} d` : r.dias_restantes === 0 ? "Vence hoy" : `En ${r.dias_restantes} día(s)`}</Tag></div>)}</div> : <Vacio titulo="Sin pagos próximos" icono={CalendarClock} />}
        </Vidrio>
        <Vidrio titulo="Stock bajo" extra={puede("inventario") && <Btn v="fant" ch onClick={() => ir("inventario")}>Ir a inventario</Btn>}>
          {d.stock_bajo.length ? <div className="col s">{d.stock_bajo.map((s) => <div key={s.id} className="fila sb"><b>{s.nombre}</b><Tag c="r">{numero(s.disponible)} {s.unidad} · mín. {numero(s.minimo)}</Tag></div>)}</div> : <Vacio titulo="Todo el stock está en orden" icono={Boxes} />}
        </Vidrio>
      </div>
    </>;
  }}</Cargando>;
}

/* ---------------- Ventas ---------------- */
let perVentas = periodo("hoy"), medioVentas = "";
export function Ventas() {
  const [per, setPer] = useState(perVentas);
  const [medio, setMedio] = useState(medioVentas);
  useEffect(() => { perVentas = per; medioVentas = medio; }, [per, medio]);
  const opc = useCarga(opciones, []);
  const carga = useCarga(() => get("/api/ventas?" + qs({ desde: per.desde, hasta: per.hasta, medio })), [per.desde, per.hasta, medio]);
  return <>
    <Encabezado titulo="Ventas" sub="Pedidos (incluido el historial de cierres) y ventas manuales">
      <Btn icono={FileSpreadsheet} onClick={() => descargar("/api/exportar.xlsx?" + qs({ tipo: "ventas", desde: per.desde, hasta: per.hasta }))}>Excel</Btn>
      <Btn v="prim" icono={Plus} onClick={() => formVenta(null, carga.recargar)}>Venta manual</Btn>
    </Encabezado>
    <BarraPeriodo valor={per} onChange={setPer}>
      <select className="inp" value={medio} onChange={(e) => setMedio(e.target.value)}><option value="">Todos los medios</option>{(opc.datos ? opc.datos.medios : []).map((x) => <option key={x}>{x}</option>)}</select>
    </BarraPeriodo>
    <Cargando carga={carga}>{(r) => {
      const filas = r.pedidos.map((p) => ({ tipo: "pedido", fecha: p.jornada, hora: hora_(p.created_at), desc: `N° ${p.id} · ${p.address}`, sub: `${p.courier_name || "Sin repartidor"} · ${p.status_label}${p.cerrado ? "" : " · abierto"}`, medio: p.medio, total: p.neto_cents, envio: p.envio_cents, id: p.id }))
        .concat(r.manuales.map((v) => ({ tipo: "manual", fecha: v.fecha, hora: v.hora, desc: v.concepto, sub: `${numero(v.cantidad)} × ${plata(v.precio_unit_cents)}${v.observaciones ? " · " + v.observaciones : ""}${v.origen === "caja" ? " · importada" : ""}`, medio: v.metodo_pago, total: v.total_cents, envio: 0, id: v.id, raw: v })))
        .sort((a, b) => (b.fecha + b.hora).localeCompare(a.fecha + a.hora));
      const total = r.total_pedidos_cents + r.total_manuales_cents;
      return <>
        <div className="grilla g4 mb">
          <Kpi destacado c="p" icono={TrendingUp} label="Total vendido" valor={plata(total)} sub={textoPeriodo(per)} />
          <Kpi c="v" icono={ShoppingBag} label="Pedidos" valor={plata(r.total_pedidos_cents)} sub={`${r.pedidos.length} pedido(s), sin envíos`} />
          <Kpi c="b" icono={Receipt} label="Ventas manuales" valor={plata(r.total_manuales_cents)} sub={`${r.manuales.length} registro(s)`} />
          <Kpi c="g" icono={Bike} label="Envíos" valor={plata(r.total_envios_cents)} sub="De los repartidores, no del local" />
        </div>
        <Tabla filas={filas} vacio="No hay ventas en este período" pie={["", "", `${filas.length} venta(s)`, "", plata(total), ""]}
          columnas={[{ t: "Fecha", v: (f) => <>{fecha(f.fecha)} <span className="t3 small">{f.hora}</span></> }, { t: "Tipo", v: (f) => f.tipo === "pedido" ? <Tag c="v">Pedido</Tag> : <Tag c="p">Manual</Tag> },
            { t: "Detalle", v: (f) => <><div className="b7">{f.desc}</div><div className="small t3">{f.sub}</div></> }, { t: "Pago", v: (f) => f.medio || "—" },
            { t: "Total", der: true, v: (f) => <><b>{plata(f.total)}</b>{f.envio > 0 && <div className="small t3">+ envío {plata(f.envio)}</div>}</> },
            { t: "", min: true, v: (f) => f.tipo === "manual" ? <div className="acc"><Btn icono={Pencil} title="Editar" onClick={() => formVenta(f.raw, carga.recargar)} /><Btn className="del" icono={Trash2} title="Eliminar" onClick={async () => { if (await confirmar("¿Eliminar esta venta manual?", { ok: "Eliminar", peligro: true }) && await intentar(() => del(`/api/ventas-manuales/${f.id}`))) { toast("Venta eliminada", "", "ok"); carga.recargar(); } }} /></div>
              : (puede("pedidos") || puede("pos")) ? <div className="acc"><Btn icono={Printer} title="Reimprimir ticket" onClick={() => imprimirTicket(f.id, 1)} /></div> : null }]} />
      </>;
    }}</Cargando>
  </>;
}
const hora_ = (iso) => String(iso || "").replace("T", " ").slice(11, 16);

async function formVenta(v, alGuardar) {
  const op = await opciones();
  const ahora = new Date();
  await formulario({
    titulo: v ? "Editar venta manual" : "Nueva venta manual",
    intro: !v && <p className="t2 small" style={{ marginTop: 0 }}>Para ventas que no pasan por la caja (eventos, mostrador, mayoristas). Los pedidos de la caja se cuentan solos.</p>,
    campos: [
      { id: "fecha", label: "Fecha", tipo: "fecha", valor: v ? v.fecha : hoy() },
      { id: "hora", label: "Hora", tipo: "hora", valor: v ? v.hora : `${String(ahora.getHours()).padStart(2, "0")}:${String(ahora.getMinutes()).padStart(2, "0")}` },
      { id: "concepto", label: "Qué se vendió", valor: v ? v.concepto : "", full: true, requerido: true, placeholder: "Ej: 20 hamburguesas para evento" },
      { id: "cantidad", label: "Cantidad", tipo: "numero", valor: v ? v.cantidad : 1, min: 0 },
      { id: "precio_unit", label: "Precio unitario", tipo: "dinero", valor: v ? pesos(v.precio_unit_cents) : "" },
      { id: "total", label: "Total", tipo: "dinero", valor: v ? pesos(v.total_cents) : "", ayuda: "Si lo dejás vacío se calcula solo." },
      { id: "metodo_pago", label: "Medio de pago", tipo: "select", valor: v ? v.metodo_pago : op.medios[0], opciones: [...new Set([...(v && v.metodo_pago ? [v.metodo_pago] : []), ...op.medios])] },
      { id: "observaciones", label: "Observaciones", tipo: "textarea", valor: v ? v.observaciones : "", full: true },
    ],
    guardar: async (d) => { if (v) await put(`/api/ventas-manuales/${v.id}`, d); else await post("/api/ventas-manuales", d); toast(v ? "Venta actualizada" : "Venta registrada", "", "ok"); alGuardar(); },
  });
}

/* ---------------- Gastos ---------------- */
let perGastos = periodo("mes"), catG = "", medioG = "";
export function Gastos({ params }) {
  const [tab, setTab] = useState((params && params.tab) || "gastos");
  return <>
    <Encabezado titulo="Gastos" sub="Lo que sale del negocio, por categoría y medio de pago" />
    <Tabs valor={tab} onChange={setTab} tabs={[["gastos", "Gastos"], ["fijos", "Pagos fijos y recordatorios"]]} />
    {tab === "gastos" ? <ListaGastos /> : <PagosFijos />}
  </>;
}
function ListaGastos() {
  const [per, setPer] = useState(perGastos);
  const [cat, setCat] = useState(catG);
  const [medio, setMedio] = useState(medioG);
  useEffect(() => { perGastos = per; catG = cat; medioG = medio; }, [per, cat, medio]);
  const opc = useCarga(opciones, []);
  const carga = useCarga(() => get("/api/gastos?" + qs({ desde: per.desde, hasta: per.hasta, categoria: cat, medio })), [per.desde, per.hasta, cat, medio]);
  const o = opc.datos || { categorias_gasto: [], medios: [] };
  return <>
    <BarraPeriodo valor={per} onChange={setPer}>
      <select className="inp" value={cat} onChange={(e) => setCat(e.target.value)}><option value="">Todas las categorías</option>{o.categorias_gasto.map((x) => <option key={x}>{x}</option>)}</select>
      <select className="inp" value={medio} onChange={(e) => setMedio(e.target.value)}><option value="">Todos los medios</option>{o.medios.map((x) => <option key={x}>{x}</option>)}</select>
      <span className="crece" />
      <Btn icono={FileSpreadsheet} onClick={() => descargar("/api/exportar.xlsx?" + qs({ tipo: "gastos", desde: per.desde, hasta: per.hasta }))}>Excel</Btn>
      <Btn v="prim" icono={Plus} onClick={() => formGasto(null, carga.recargar)}>Nuevo gasto</Btn>
    </BarraPeriodo>
    <Cargando carga={carga}>{(r) => {
      const porCat = {}; r.gastos.forEach((g) => { const k = g.categoria || "Sin categoría"; porCat[k] = (porCat[k] || 0) + g.importe_cents; });
      const top = Object.entries(porCat).sort((a, b) => b[1] - a[1])[0];
      return <>
        <div className="grilla g3 mb">
          <Kpi destacado c="r" icono={Receipt} label="Total gastado" valor={plata(r.total_cents)} sub={textoPeriodo(per)} />
          <Kpi c="v" icono={Receipt} label="Cantidad de gastos" valor={String(r.gastos.length)} sub={r.gastos.length ? `Promedio ${plata(Math.round(r.total_cents / r.gastos.length))}` : ""} />
          <Kpi c="a" icono={TrendingUp} label="Categoría principal" valor={top ? top[0] : "—"} sub={top ? plata(top[1]) : ""} />
        </div>
        <Tabla filas={r.gastos} vacio="No hay gastos en este período" vacioSub="Registralos con «Nuevo gasto»." pie={["", "", `${r.gastos.length} gasto(s)`, "", "", plata(r.total_cents), ""]}
          columnas={[{ t: "Fecha", v: (g) => fecha(g.fecha) }, { t: "Categoría", v: (g) => g.categoria ? <Tag>{g.categoria}</Tag> : "—" },
            { t: "Descripción", v: (g) => <><div className="b7">{g.descripcion}</div>{g.observaciones && <div className="small t3">{g.observaciones}</div>}</> },
            { t: "Proveedor", v: (g) => g.proveedor || "—" }, { t: "Pago", v: (g) => g.metodo_pago || "—" },
            { t: "Importe", der: true, v: (g) => <b className={r.umbral_cents && g.importe_cents > r.umbral_cents ? "mal-t" : ""}>{plata(g.importe_cents)}</b> },
            { t: "", min: true, v: (g) => <div className="acc"><Btn icono={Pencil} title="Editar" onClick={() => formGasto(g, carga.recargar)} /><Btn className="del" icono={Trash2} title="Eliminar" onClick={async () => { if (await confirmar(`¿Eliminar el gasto «${g.descripcion}» (${plata(g.importe_cents)})?`, { ok: "Eliminar", peligro: true }) && await intentar(() => del(`/api/gastos/${g.id}`))) { toast("Gasto eliminado", "", "ok"); carga.recargar(); } }} /></div> }]} />
      </>;
    }}</Cargando>
  </>;
}
async function formGasto(g, alGuardar) {
  const op = await opciones();
  await formulario({
    titulo: g ? "Editar gasto" : "Nuevo gasto",
    campos: [
      { id: "fecha", label: "Fecha", tipo: "fecha", valor: g ? g.fecha : hoy() },
      { id: "categoria", label: "Categoría", tipo: "select", valor: g ? g.categoria : "", opciones: [["", "Sin categoría"], ...[...new Set([...(g && g.categoria ? [g.categoria] : []), ...op.categorias_gasto])].map((x) => [x, x])] },
      { id: "descripcion", label: "Descripción", valor: g ? g.descripcion : "", full: true, requerido: true, placeholder: "Ej: Compra de carne para la semana" },
      { id: "proveedor", label: "Proveedor", valor: g ? g.proveedor : "" },
      { id: "importe", label: "Importe", tipo: "dinero", valor: g ? pesos(g.importe_cents) : "", requerido: true },
      { id: "metodo_pago", label: "Medio de pago", tipo: "select", valor: g ? g.metodo_pago : op.medios[0], opciones: [...new Set([...(g && g.metodo_pago ? [g.metodo_pago] : []), ...op.medios])] },
      { id: "observaciones", label: "Observaciones", tipo: "textarea", valor: g ? g.observaciones : "", full: true },
    ],
    guardar: async (d) => {
      if (g) { await put(`/api/gastos/${g.id}`, d); toast("Gasto actualizado", "", "ok"); }
      else { const r = await post("/api/gastos", d); toast("Gasto registrado", d.descripcion, "ok"); if (r.elevado) toast("Gasto elevado", "Supera el aviso configurado.", "aviso"); }
      alGuardar();
    },
  });
}
function PagosFijos() {
  const carga = useCarga(() => Promise.all([get("/api/recordatorios"), opciones()]), []);
  const recargar = () => { carga.recargar(); bus.emit("avisos"); };
  return <Cargando carga={carga}>{([r, op]) => {
    const form = (x) => formulario({
      titulo: x ? "Editar pago fijo" : "Nuevo pago fijo",
      campos: [{ id: "servicio", label: "Qué se paga", valor: x ? x.servicio : "", full: true, requerido: true, placeholder: "Ej: Alquiler" },
        { id: "dia", label: "Día de vencimiento", tipo: "numero", valor: x ? x.dia : 10, min: 1, max: 31 }, { id: "monto", label: "Monto estimado", tipo: "dinero", valor: x && x.monto_cents ? pesos(x.monto_cents) : "" },
        { id: "categoria", label: "Categoría del gasto", tipo: "select", valor: x ? x.categoria : "", opciones: [["", "Sin categoría"], ...op.categorias_gasto.map((c) => [c, c])] },
        { id: "activo", label: "Activo (avisar)", tipo: "check", valor: x ? !!x.activo : true }],
      guardar: async (d) => { d.activo = d.activo ? 1 : 0; if (x) await put(`/api/recordatorios/${x.id}`, d); else await post("/api/recordatorios", d); recargar(); },
    });
    const pagar = (x) => formulario({
      titulo: `Pago de ${x.servicio}`, ok: "Marcar pagado",
      campos: [{ id: "importe", label: "Importe pagado", tipo: "dinero", valor: x.monto_cents ? pesos(x.monto_cents) : "" }, { id: "fecha", label: "Fecha", tipo: "fecha", valor: hoy() },
        { id: "metodo_pago", label: "Medio de pago", tipo: "select", valor: op.medios[0], opciones: op.medios }, { id: "registrar_gasto", label: "Registrarlo como gasto", tipo: "check", valor: true }],
      guardar: async (d) => { await post(`/api/recordatorios/${x.id}/pagar`, { ...d, periodo: x.periodo, registrar_gasto: d.registrar_gasto ? 1 : 0 }); toast("Pago registrado", x.servicio, "ok"); recargar(); },
    });
    return <>
      <div className="filtros"><span className="t2 small">Alquiler, servicios, sueldos… Avisa {r.dias_aviso} día(s) antes del vencimiento. Al marcarlo pagado queda como gasto.</span><span className="crece" /><Btn v="prim" icono={Plus} onClick={() => form(null)}>Nuevo pago fijo</Btn></div>
      <Tabla filas={r.recordatorios} apagada={(x) => !x.activo} vacio="No hay pagos fijos cargados" vacioSub="Agregá alquiler, luz, internet… y te avisa antes de cada vencimiento."
        columnas={[{ t: "Servicio", v: (x) => <><b>{x.servicio}</b> {x.categoria && <Tag>{x.categoria}</Tag>}</> }, { t: "Vence", v: (x) => `Día ${x.dia} de cada mes` },
          { t: "Monto estimado", der: true, v: (x) => x.monto_cents ? plata(x.monto_cents) : "—" },
          { t: "Estado", v: (x) => !x.activo ? <Tag>Pausado</Tag> : x.vence ? <Tag c={x.vencido ? "r" : x.dias_restantes <= r.dias_aviso ? "a" : ""}>{x.vencido ? `Vencido (${fecha(x.vence)})` : `Vence ${fecha(x.vence)}`}</Tag> : <Tag c="g">Al día</Tag> },
          { t: "", min: true, v: (x) => <div className="fila" style={{ gap: 4, justifyContent: "flex-end" }}>{x.activo && x.vence && <Btn ch v="ok" icono={Check} onClick={() => pagar(x)}>Marcar pagado</Btn>}
            <div className="acc"><Btn icono={Pencil} onClick={() => form(x)} /><Btn className="del" icono={Trash2} onClick={async () => { if (await confirmar("¿Eliminar este pago fijo?", { ok: "Eliminar", peligro: true }) && await intentar(() => del(`/api/recordatorios/${x.id}`))) recargar(); }} /></div></div> }]} />
    </>;
  }}</Cargando>;
}

/* ---------------- Reportes ---------------- */
let perRep = periodo("mes");
export function Reportes() {
  const [per, setPer] = useState(perRep);
  const [menu, setMenu] = useState(false);
  useEffect(() => { perRep = per; }, [per]);
  const carga = useCarga(() => Promise.all([get("/api/reportes?" + qs({ desde: per.desde, hasta: per.hasta })), get("/api/cierres").catch(() => ({ cierres: [] }))]), [per.desde, per.hasta]);
  const url = (tipo, ext) => `/api/exportar.${ext}?` + qs({ tipo, desde: per.desde, hasta: per.hasta });
  const tipos = [["completo", "Reporte completo"], ["ventas", "Solo ventas"], ["gastos", "Solo gastos"], ["balance", "Balance"]];
  return <>
    <Encabezado titulo="Reportes" sub="Ganancias, comparaciones y exportación a Excel o PDF">
      <div className="rel"><Btn v="prim" icono={Download} onClick={() => setMenu(!menu)}>Exportar</Btn>
        <Desplegable abierto={menu} onClose={() => setMenu(false)} ancho={260}>
          <div className="menu-t">Excel (.xlsx)</div>{tipos.map(([t, n]) => <button key={"x" + t} className="menu-i" onClick={() => { descargar(url(t, "xlsx")); setMenu(false); toast("Generando Excel", n, "ok"); }}><FileSpreadsheet />{n}</button>)}
          <div className="menu-sep" /><div className="menu-t">PDF</div>{tipos.map(([t, n]) => <button key={"p" + t} className="menu-i" onClick={() => { descargar(url(t, "pdf")); setMenu(false); toast("Generando PDF", n, "ok"); }}><FileText />{n}</button>)}
        </Desplegable></div>
    </Encabezado>
    <BarraPeriodo valor={per} onChange={setPer} />
    <Cargando carga={carga}>{([r, c]) => {
      const s = r.resumen, g = r.ganancias;
      const dias = s.serie_dias.length > 62 ? s.serie_dias.slice(-62) : s.serie_dias;
      return <>
        <div className="grilla g4">
          {[["dia", "Ganancia de hoy"], ["semana", "Ganancia de la semana"], ["mes", "Ganancia del mes"], ["anio", "Ganancia del año"]].map(([k, t]) => <Kpi key={k} c={g[k] >= 0 ? "g" : "r"} icono={Wallet} label={t} valor={plata(g[k])} />)}
        </div>
        <h3 className="mt-l">Período: {textoPeriodo(per)}</h3>
        <div className="grilla g4">
          <Kpi destacado c="p" icono={TrendingUp} label="Total vendido" valor={plata(s.ventas_cents)} sub={`Pedidos ${plata(s.ventas_pedidos_cents)} · manuales ${plata(s.ventas_manuales_cents)}`} />
          <Kpi c="r" icono={Receipt} label="Total gastado" valor={plata(s.gastos_cents)} sub={`${s.gastos_count} gasto(s)`} />
          <Kpi c={s.balance_cents >= 0 ? "g" : "r"} icono={Wallet} label="Balance" valor={plata(s.balance_cents)} sub="Vendido − gastado" />
          <Kpi c="v" icono={ShoppingBag} label="Pedidos" valor={String(s.pedidos_count)} sub={`Ticket promedio ${plata(s.ticket_promedio_cents)}`} />
        </div>
        <div className="grilla g2 mt">
          <Vidrio titulo="Últimos 6 meses"><Barras etiquetas={r.meses.map((x) => x.mes)} series={[{ nombre: "Ventas", color: COLORES[0], valores: r.meses.map((x) => x.ventas_cents) }, { nombre: "Gastos", color: COLORES[1], valores: r.meses.map((x) => x.gastos_cents) }, { nombre: "Ganancia", color: COLORES[3], valores: r.meses.map((x) => x.balance_cents) }]} /></Vidrio>
          <Vidrio titulo="Ventas y gastos por día">{dias.length ? <Barras etiquetas={dias.map((x) => fechaCorta(x.dia))} series={[{ nombre: "Ventas", color: COLORES[0], valores: dias.map((x) => x.ventas) }, { nombre: "Gastos", color: COLORES[1], valores: dias.map((x) => x.gastos) }]} /> : <Vacio titulo="Sin movimientos en el período" />}</Vidrio>
        </div>
        <div className="grilla g2 mt">
          <Vidrio titulo="Gastos por categoría"><Dona items={s.gastos_por_categoria.map((x) => ({ etiqueta: x.categoria, valor: x.total_cents }))} vacio="Sin gastos en el período" /></Vidrio>
          <Vidrio titulo="Ventas por medio de pago"><Dona items={s.ventas_por_medio.map((x) => ({ etiqueta: x.medio, valor: x.total_cents }))} vacio="Sin ventas en el período" /></Vidrio>
        </div>
        <div className="grilla g2 mt">
          <div><h3>Productos más vendidos</h3><Tabla filas={s.top_productos} vacio="Sin ventas en el período" columnas={[{ t: "#", v: (_, i) => i + 1 }, { t: "Producto", v: (p) => <>{p.nombre} {p.promo && <Tag c="p">promo</Tag>}</> }, { t: "Unidades", der: true, v: (p) => numero(p.unidades) }, { t: "Total", der: true, v: (p) => <b>{plata(p.total_cents)}</b> }]} /></div>
          <div><h3>Por repartidor</h3><Tabla filas={s.por_repartidor} vacio="Sin pedidos en el período" columnas={[{ t: "Repartidor", k: "nombre" }, { t: "Pedidos", k: "pedidos", der: true }, { t: "Ventas", der: true, v: (x) => plata(x.ventas_cents) }, { t: "Envíos", der: true, v: (x) => plata(x.envios_cents) }]} /></div>
        </div>
        <h3 className="mt-l">Historial de cierres</h3>
        <Tabla filas={c.cierres.filter((x) => (!per.desde || x.jornada >= per.desde) && (!per.hasta || x.jornada <= per.hasta))} vacio="Sin cierres en el período"
          columnas={[{ t: "N°", v: (x) => <b>#{x.id}</b> }, { t: "Jornada", v: (x) => fecha(x.jornada) }, { t: "Cerró", v: (x) => <>{x.user_name || "—"} <span className="t3 small">{fechaHora(x.created_at)}</span></> }, { t: "Pedidos", k: "orders_count", der: true }, { t: "Facturado", der: true, v: (x) => plata(x.gross_total_cents) }, { t: "Rendido", der: true, v: (x) => <b>{plata(x.cash_to_render_cents)}</b> }, { t: "", min: true, v: (x) => <Btn ch icono={FileText} onClick={() => descargar(`/api/cierres/${x.id}/pdf`)}>PDF</Btn> }]} />
      </>;
    }}</Cargando>
  </>;
}
export { useRef };
