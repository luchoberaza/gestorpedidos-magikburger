import { useCallback, useEffect, useId, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, Banknote, Bike, Clock, FileDown, Lock, Pencil, Phone, Plus, Printer, Search, Trash2, TrendingUp, Wallet, ArrowLeftRight } from "lucide-react";
import { bus, del, descargar, fecha, fechaHora, get, hace, hora, imprimirTicket, intentar, plata, post, puede, toast } from "../lib";
import { Aviso, Btn, Cargando, Kpi, Seg, Tabla, Tag, Vacio, confirmar, resorte, useCarga } from "../ui";
import { editarPedido } from "./Pos";

const ETQ = { new: "Nuevo", kitchen: "Cocina", way: "En camino", done: "Entregado" };
const ESTADOS = ["new", "kitchen", "way", "done"];

export function Encabezado({ titulo, sub, children }) {
  return <div className="pag-h"><div><h1>{titulo}</h1>{sub && <p>{sub}</p>}</div>{children && <div className="fila wrap">{children}</div>}</div>;
}

/* ---------------- Pedidos ---------------- */
export function Pedidos({ ir, params }) {
  const [datos, setDatos] = useState(null);
  const [error, setError] = useState(null);
  const [filtro, setFiltro] = useState("todos");
  const [texto, setTexto] = useState(params && params.buscar && params.buscar.id ? String(params.buscar.id) : "");
  const cargar = useCallback(() => get("/api/board").then((d) => { setDatos(d); setError(null); }).catch(setError), []);
  useEffect(() => { cargar(); const t = setInterval(() => { if (!document.querySelector(".capa")) cargar(); }, 20000); return () => clearInterval(t); }, [cargar]);

  if (error) return <Aviso c="r">{error.message}</Aviso>;
  if (!datos) return <div className="t3">Cargando…</div>;
  const todos = ESTADOS.flatMap((s) => datos[s] || []).sort((a, b) => b.id - a.id);
  const cuenta = Object.fromEntries(ESTADOS.map((s) => [s, (datos[s] || []).length]));
  const q = texto.toLowerCase();
  const lista = todos.filter((o) => (filtro === "todos" || (filtro === "curso" ? o.status !== "done" : o.status === filtro))
    && (!q || String(o.id) === q || (o.address || "").toLowerCase().includes(q) || (o.phone || "").toLowerCase().includes(q)));
  const viejos = todos.filter((o) => o.anterior).length;

  const estado = async (o, st) => {
    setDatos((d) => { const n = { ...d }; ESTADOS.forEach((s) => { n[s] = (d[s] || []).filter((x) => x.id !== o.id); }); n[st] = [...n[st], { ...o, status: st }]; return n; });
    const r = await intentar(() => post(`/api/orders/${o.id}/status`, { status: st }), "No se pudo cambiar el estado");
    if (r && st === "done") toast(`Pedido N° ${o.id} entregado`, o.address, "ok");
    cargar();
  };
  const eliminar = async (o) => {
    if (!(await confirmar(`¿Eliminar el pedido N° ${o.id} (${o.address}, ${plata(o.total_cents)})?\nQueda registrado en la auditoría.`, { titulo: "Eliminar pedido", ok: "Eliminar", peligro: true }))) return;
    if (await intentar(() => post(`/api/orders/${o.id}/delete`), "No se pudo eliminar")) { toast(`Pedido N° ${o.id} eliminado`, "", "ok"); cargar(); bus.emit("avisos"); }
  };

  return (
    <>
      <Encabezado titulo="Pedidos" sub="Control de los pedidos abiertos de la jornada">
        <div className="inp-ic" style={{ width: 300 }}><Search /><input className="inp" type="search" placeholder="Dirección, teléfono o N°" value={texto} onChange={(e) => setTexto(e.target.value)} /></div>
        {puede("pos") && <Btn v="prim" icono={Plus} onClick={() => ir("pos")}>Nuevo pedido</Btn>}
      </Encabezado>
      <div className="filtros">
        <Seg valor={filtro} onChange={setFiltro} opciones={[["todos", <>Todos <span className="n">{todos.length}</span></>], ["curso", <>En curso <span className="n">{cuenta.new + cuenta.kitchen + cuenta.way}</span></>], ...ESTADOS.map((s) => [s, <>{ETQ[s]} <span className="n">{cuenta[s]}</span></>])]} />
      </div>
      {viejos > 0 && <div className="mb"><Aviso c="a"><b>{viejos} pedido(s) de jornadas anteriores siguen abiertos.</b> No se suman a la liquidación de hoy: se cierran con el cierre de jornada, o eliminá los que no correspondan.</Aviso></div>}
      {lista.length ? (
        <motion.div layout className="tarjetas">
          <AnimatePresence>
            {lista.map((o) => <TarjetaPedido key={o.id} o={o} onEstado={(st) => estado(o, st)} onEliminar={() => eliminar(o)} onEditar={() => intentar(() => editarPedido(o.id, cargar), "No se pudo abrir el pedido")} />)}
          </AnimatePresence>
        </motion.div>
      ) : <div className="vidrio"><Vacio titulo={todos.length ? "No hay pedidos con ese filtro" : "No hay pedidos abiertos"} sub={todos.length ? "" : "Los pedidos que se tomen aparecen acá hasta el cierre de la jornada."} /></div>}
    </>
  );
}

function TarjetaPedido({ o, onEstado, onEditar, onEliminar }) {
  const id = useId();
  return (
    <motion.div layout className={`ped ${o.status === "done" ? "done" : ""} ${o.anterior ? "viejo" : ""}`} initial={{ opacity: 0, scale: .97 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: .96 }} transition={{ duration: .18 }}>
      <div className="fila top">
        <div className="crece">
          <div className="fila wrap" style={{ gap: 6 }}><Tag>N° {o.id}</Tag>{o.anterior && <Tag c="a">Jornada anterior</Tag>}<Tag c={o.es_efectivo ? "g" : "b"} icono={o.es_efectivo ? Banknote : ArrowLeftRight}>{o.medio}</Tag></div>
          <div className="dir mt-s">{o.address}</div>
        </div>
        <div className="monto">{plata(o.total_cents)}</div>
      </div>
      <div className="meta"><span><Phone />{o.phone}</span><span><Bike />{o.courier_name || "Sin asignar"}</span><span><Clock />{hora(o.created_at)} · {hace(o.created_at)}</span><span>{o.unidades} u.</span></div>
      {o.paga_con_cents > 0 && o.es_efectivo && <div className="meta"><span><Banknote />Paga con {plata(o.paga_con_cents)} · <b className="ok-t">vuelto {plata(o.paga_con_cents - o.total_cents)}</b></span></div>}
      {o.notes && <div className="nota">📝 {o.notes}</div>}
      <div className="estados">
        {ESTADOS.map((s) => <button key={s} type="button" className={o.status === s ? "on" : ""} onClick={() => o.status !== s && onEstado(s)}>
          {o.status === s && <motion.span layoutId={`est-${id}`} className={`est-ind est-${s}`} transition={resorte} />}<span>{ETQ[s]}</span></button>)}
      </div>
      <div className="fila">
        <Btn ch icono={Printer} onClick={() => imprimirTicket(o.id, 1)}>Imprimir</Btn>
        <Btn ch icono={Pencil} onClick={onEditar}>Editar</Btn>
        <span className="crece" />
        {puede("pedidos_eliminar") && <Btn ch v="peligro" icono={Trash2} onClick={onEliminar}>Eliminar</Btn>}
      </div>
    </motion.div>
  );
}

/* ---------------- Liquidación ---------------- */
export function Liquidacion() {
  const carga = useCarga(() => Promise.all([get("/api/liquidation"), get("/api/cierres").catch(() => ({ cierres: [] }))]), []);
  return <Cargando carga={carga}>{([l, c]) => <VistaLiquidacion l={l} cierres={c.cierres} recargar={carga.recargar} />}</Cargando>;
}

function VistaLiquidacion({ l, cierres, recargar }) {
  const cerrar = async () => {
    const r = await confirmar("Se genera el PDF del cierre, los pedidos pasan al historial (no se borra nada), se descuenta el stock según las recetas y se hace un respaldo.", {
      titulo: "Cerrar la jornada", ok: "Cerrar jornada",
      extra: <div className="col mt">
        {l.pending_count > 0 && <Aviso c="a">Hay <b>{l.pending_count} pedido(s) sin entregar</b>. Se cierran igual y quedan en el historial.</Aviso>}
        <div className="vidrio pad"><div className="fila sb"><span>Efectivo a rendir</span><b className="num">{plata(l.cash_to_render_cents)}</b></div><div className="fila sb small t2"><span>Pedidos abiertos que se cierran</span><span>{l.abiertos_total}</span></div></div>
      </div>,
      checks: [{ name: "ajustes", label: "Incluir envíos y descuentos en el detalle del PDF" }],
    });
    if (!r) return;
    const res = await intentar(() => post("/api/cierre", { include_adjustments: !!r.ajustes }), "No se pudo cerrar la jornada");
    if (!res) return;
    descargar(res.pdf_url);
    toast(`Jornada cerrada (cierre N° ${res.cierre_id})`, `${res.pedidos} pedido(s) pasaron al historial. Se está descargando el PDF.`, "ok");
    bus.emit("avisos"); recargar();
  };
  return (
    <>
      <Encabezado titulo="Liquidación" sub={`Jornada del ${fecha(l.jornada)} · A rendir = efectivo entregado − envíos + cambios`}>
        {puede("cierre") && <Btn v="prim" icono={Lock} disabled={!l.abiertos_total} onClick={cerrar}>Cerrar jornada</Btn>}
      </Encabezado>
      <div className="grilla g4">
        <Kpi destacado grande c="p" icono={Wallet} label="Efectivo a rendir" valor={plata(l.cash_to_render_cents)} sub="Lo que tiene que entrar a la caja" />
        <Kpi c="v" icono={TrendingUp} label="Facturado" valor={plata(l.total_cents)} sub={`${l.orders_count} pedido(s) · ${l.delivered_count} entregado(s)`} />
        <Kpi c="b" icono={ArrowLeftRight} label="Otros medios" valor={plata(l.transfer_cents)} sub="Transferencias y similares: no se rinden" />
        <Kpi c="g" icono={Bike} label="Envíos" valor={plata(l.delivery_total_cents)} sub="Quedan para los repartidores" />
      </div>
      <div className="col mt">
        {l.pending_count > 0 && <Aviso c="a" icono={Clock}><b>{l.pending_count} pedido(s) sin entregar ({plata(l.pending_cents)}).</b> No entran en el efectivo a rendir: el repartidor todavía no cobró.</Aviso>}
        {l.carryover_count > 0 && <Aviso c="r" icono={AlertTriangle}><b>{l.carryover_count} pedido(s) de jornadas anteriores siguen abiertos ({plata(l.carryover_cents)}).</b> Están fuera del cálculo de hoy; se cierran junto con esta jornada.</Aviso>}
      </div>
      <h3 className="mt-l">Por repartidor</h3>
      {l.couriers.length ? <div className="grilla g2">{l.couriers.map((x) => (
        <motion.div key={x.courier_name} className="vidrio pad" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
          <div className="rep">
            <div><div className="r-n">{x.courier_name}</div><div className="t2 small">{x.delivered_count} entregado(s){x.pending_count ? <span className="av-t"> · {x.pending_count} sin entregar ({plata(x.pending_cents)})</span> : null}</div></div>
            <div><div className="t3 tiny b7" style={{ textAlign: "right", letterSpacing: ".6px" }}>A RENDIR</div><div className="r-v grad-t">{plata(x.cash_cents)}</div></div>
            <div className="cuenta" style={{ gridColumn: "1/-1" }}><span>Cobró <b>{plata(x.cash_collected_cents)}</b></span>{x.delivery_cents > 0 && <span>− envíos <b>{plata(x.delivery_cents)}</b></span>}{x.cash_float_cents > 0 && <span>+ cambio <b>{plata(x.cash_float_cents)}</b></span>}</div>
            {(x.transfer_cents > 0 || x.delivery_all_cents > 0) && <div className="t3 small" style={{ gridColumn: "1/-1" }}>
              {x.transfer_cents > 0 && <>Otros medios: {plata(x.transfer_cents)} (ya están en la cuenta del local)</>}{x.transfer_cents > 0 && x.delivery_all_cents > 0 && " · "}{x.delivery_all_cents > 0 && <>Ganó en envíos: {plata(x.delivery_all_cents)}</>}</div>}
          </div>
        </motion.div>))}</div> : <div className="vidrio"><Vacio titulo="Todavía no hay pedidos en esta jornada" /></div>}
      <h3 className="mt-l">Cierres anteriores</h3>
      <Tabla filas={cierres.slice(0, 30)} vacio="Todavía no hubo cierres" vacioSub="Al cerrar la jornada, los pedidos pasan al historial y el PDF queda acá."
        columnas={[{ t: "N°", v: (x) => <b>#{x.id}</b> }, { t: "Jornada", v: (x) => fecha(x.jornada) }, { t: "Cerró", v: (x) => <>{x.user_name || "—"} <span className="t3 small">{fechaHora(x.created_at)}</span></> },
          { t: "Pedidos", k: "orders_count", der: true }, { t: "Facturado", v: (x) => plata(x.gross_total_cents), der: true }, { t: "Rendido", v: (x) => <b>{plata(x.cash_to_render_cents)}</b>, der: true },
          { t: "", min: true, v: (x) => <Btn ch icono={FileDown} onClick={() => descargar(`/api/cierres/${x.id}/pdf`)}>PDF</Btn> }]} />
    </>
  );
}

export { del };
