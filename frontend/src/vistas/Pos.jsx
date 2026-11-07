import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bike, Check, MapPin, Pencil, Phone, Plus, Printer, Search, Sparkles, StickyNote, Trash2, X, Banknote, ArrowLeftRight, CreditCard } from "lucide-react";
import { aCentavos, get, imprimirTicket, pesos, plata, post, toast } from "../lib";
import { Btn, Modal, Paso, Seg, Tag, Vacio, abrir, avisar, confirmar, formulario, resorte } from "../ui";
import { Haz, Monto, useBrillo } from "../efectos";
import ClickSpark from "../rb/ClickSpark";

// Chispa al tocar un producto (ClickSpark de React Bits), en los colores del logo.
const CHISPA = ["#ffb21e", "#ffd36b", "#86b8ff"];

const porId = (arr) => Object.fromEntries((arr || []).map((x) => [x.id, x]));
const nuevo = (pm) => ({ phone: "", address: "", courier_id: "", payment_method: pm || "cash", notes: "", items: [], adjustments: [] });
const GUARDADO = "mb_pedido_en_curso";

/* ---------------- Cálculo (idéntico al servidor) ---------------- */
function totalItem(boot, it) {
  const p = porId(boot.products)[it.product_id], ing = porId(boot.ingredients);
  let t = it.promo_group ? 0 : (p ? p.price_cents : 0);
  (it.added_ingredient_ids || []).forEach((id) => { if (ing[id]) t += ing[id].extra_price_cents; });
  return t * Math.max(1, parseInt(it.qty || 1, 10));
}
function totalPedido(boot, e) {
  let t = e.items.reduce((a, it) => a + totalItem(boot, it), 0);
  const vistos = new Set();
  e.items.forEach((it) => { if (it.promo_group && !vistos.has(it.promo_group)) { vistos.add(it.promo_group); t += it.promo_price_cents || 0; } });
  return t + e.adjustments.reduce((s, a) => s + (parseInt(a.amount_cents, 10) || 0), 0);
}
const cuerpoPedido = (boot, e) => ({
  phone: e.phone.trim(), address: e.address.trim(), notes: (e.notes || "").trim(),
  courier_id: e.courier_id ? parseInt(e.courier_id, 10) : null, payment_method: e.payment_method,
  items: e.items, adjustments: e.adjustments, client_total_cents: totalPedido(boot, e),
});
const falta = (e) => (!e.address.trim() || !e.phone.trim()) ? "Completá la dirección y el teléfono." : !e.items.length ? "El pedido está vacío: agregá al menos un producto." : null;
const iconoPago = (m) => (m.is_cash ? Banknote : m.code === "transfer" ? ArrowLeftRight : CreditCard);

/* ---------------- Editor (caja y edición) ---------------- */
function Editor({ boot, est, setEst, modo, orderId, onGuardar, guardando, refs }) {
  const [filtro, setFiltro] = useState("");
  const [busq, setBusq] = useState("");
  const [sug, setSug] = useState([]);
  const dirRef = useRef(null), busRef = useRef(null);
  const zonaBrillo = useBrillo();
  if (refs) { refs.dir = dirRef; refs.bus = busRef; }
  const prods = porId(boot.products), ing = porId(boot.ingredients);
  const pagos = boot.metodos_pago.filter((m) => m.active || m.code === est.payment_method);
  const reps = boot.couriers.filter((c) => c.active || String(c.id) === String(est.courier_id));
  const cats = useMemo(() => [...new Set(boot.products.filter((p) => p.active && p.category).map((p) => p.category))].sort(), [boot]);
  const set = (k, v) => setEst((e) => ({ ...e, [k]: v }));
  const total = totalPedido(boot, est);
  const listo = !falta(est) && !guardando; // el botón principal avisa que ya se puede guardar

  // Cliente conocido: por el teléfono se sugieren sus direcciones anteriores.
  useEffect(() => {
    const t = est.phone.replace(/\D/g, "");
    if (t.length < 6) { setSug([]); return undefined; }
    const h = setTimeout(async () => {
      const r = await get("/api/clientes?tel=" + encodeURIComponent(est.phone)).catch(() => null);
      if (!r || !r.direcciones.length) { setSug([]); return; }
      if (r.direcciones.length === 1 && !est.address.trim()) { set("address", r.direcciones[0]); toast("Cliente conocido", `${r.pedidos} pedido(s) anteriores · dirección completada`, "ok"); setSug([]); return; }
      setSug(r.direcciones.includes(est.address.trim()) ? [] : r.direcciones);
    }, 380);
    return () => clearTimeout(h);
  }, [est.phone]); // eslint-disable-line react-hooks/exhaustive-deps

  const enPedido = {}, promoCnt = {};
  est.items.forEach((it) => { if (!it.promo_group) enPedido[it.product_id] = (enPedido[it.product_id] || 0) + (parseInt(it.qty, 10) || 1); });
  new Map(est.items.filter((it) => it.promo_group).map((it) => [it.promo_group, it.promo_id])).forEach((pid) => { promoCnt[pid] = (promoCnt[pid] || 0) + 1; });
  const q = busq.toLowerCase();
  const promos = (filtro === "" || filtro === "__p") ? boot.promos.filter((p) => !q || p.name.toLowerCase().includes(q)) : [];
  const lista = filtro === "__p" ? [] : boot.products.filter((p) => p.active && (!filtro || p.category === filtro) && (!q || p.name.toLowerCase().includes(q)));

  const agregar = (pid) => setEst((e) => {
    const items = e.items.map((x) => ({ ...x }));
    const sin = items.find((it) => !it.promo_group && it.product_id === pid && !(it.removed_ingredient_ids || []).length && !(it.added_ingredient_ids || []).length);
    if (sin) sin.qty = (parseInt(sin.qty, 10) || 1) + 1;
    else items.push({ product_id: pid, qty: 1, removed_ingredient_ids: [], added_ingredient_ids: [] });
    return { ...e, items };
  });
  const agregarPromo = (pr) => {
    if (!pr.component_product_ids.length) return toast("Promo sin productos", "Configurala en Productos y promos.", "aviso");
    const g = "PR-" + Date.now().toString(36) + Math.floor(Math.random() * 1000);
    setEst((e) => ({ ...e, items: [...e.items, ...pr.component_product_ids.map((pid) => ({ product_id: pid, qty: 1, removed_ingredient_ids: [], added_ingredient_ids: [], promo_group: g, promo_id: pr.id, promo_name: pr.name, promo_price_cents: pr.price_cents }))] }));
  };
  const cambiarItem = (i, fn) => setEst((e) => { const items = e.items.map((x) => ({ ...x })); fn(items); return { ...e, items }; });
  const modificar = (i) => abrir(({ cerrar, arriba }) => <Modificar boot={boot} est={est} setEst={setEst} indice={i} cerrar={cerrar} arriba={arriba} />);
  const ajuste = async (kind) => {
    const envio = kind === "delivery", fee = boot.settings.delivery_fee_cents;
    const r = await formulario({
      titulo: envio ? "Cobrar envío" : "Agregar ajuste", tam: "ch", ok: "Agregar", foco: "monto", seleccionar: true,
      intro: <p className="t2 small" style={{ marginTop: 0 }}>{envio ? "El envío es del repartidor: se descuenta del efectivo que rinde." : <>Positivo suma (recargo). Negativo resta (descuento). Ej: <b>-200</b></>}</p>,
      campos: [
        { id: "label", label: "Motivo", valor: envio ? "Envío" : "", placeholder: envio ? "Envío" : "Ej: Descuento, Sin papas…", full: true },
        { id: "monto", label: "Monto", tipo: "dinero", valor: envio && fee ? pesos(fee) : "", full: true, requerido: true },
      ],
      guardar: async (d) => {
        let c = aCentavos(d.monto);
        if (c === null || c === 0) throw new Error("Ingresá un monto válido distinto de cero (ej: 500 o -200).");
        if (envio && c < 0) c = -c;
        return { label: (d.label || (c < 0 ? "Descuento" : envio ? "Envío" : "Ajuste")).slice(0, 60), amount_cents: c, kind: envio ? "delivery" : "" };
      },
    });
    if (!r) return;
    setEst((e) => ({ ...e, adjustments: [...e.adjustments, r] }));
    if (envio && r.amount_cents !== fee) { boot.settings.delivery_fee_cents = r.amount_cents; post("/api/config/preferencia", { delivery_fee_cents: r.amount_cents }).catch(() => {}); }
  };

  // Bloques: ítems sueltos y promos agrupadas.
  const bloques = [], idx = {};
  est.items.forEach((it, i) => {
    if (it.promo_group) {
      if (!(it.promo_group in idx)) { idx[it.promo_group] = bloques.length; bloques.push({ promo: true, grupo: it.promo_group, nombre: it.promo_name, precio: it.promo_price_cents || 0, comps: [] }); }
      bloques[idx[it.promo_group]].comps.push(i);
    } else bloques.push({ promo: false, i });
  });
  const unidades = est.items.reduce((a, it) => a + (it.promo_group ? 0 : (parseInt(it.qty, 10) || 1)), 0) + Object.keys(idx).length;
  const mods = (it) => {
    const cnt = {}; (it.added_ingredient_ids || []).forEach((id) => { cnt[id] = (cnt[id] || 0) + 1; });
    const q2 = (it.removed_ingredient_ids || []).map((id) => ing[id]).filter(Boolean).map((x) => <span key={"q" + x.id} className="mod q">Sin {x.name}</span>);
    const a2 = Object.keys(cnt).map((id) => ing[id]).filter(Boolean).map((x) => <span key={"a" + x.id} className="mod a">+ {x.name}{cnt[x.id] > 1 ? ` x${cnt[x.id]}` : ""}</span>);
    return q2.length || a2.length ? <div className="mods">{q2}{a2}</div> : null;
  };

  return (
    <div className="caja">
      <div className="caja-izq">
        <div className="vidrio">
          <div className="vidrio-h"><h3>{modo === "nuevo" ? "Nuevo pedido" : `Pedido N° ${orderId}`}</h3><Tag>{modo === "nuevo" ? "Ingresar nuevo pedido" : "Editar"}</Tag></div>
          <div className="vidrio-b cliente">
            <div className="campo rel"><label><MapPin size={14} /> Dirección <span className="kbd">F2</span></label>
              <input ref={dirRef} className="inp" value={est.address} maxLength={160} placeholder="Ej: Brasil 1234 apto 2" onChange={(e) => set("address", e.target.value)} autoComplete="off" />
              <AnimatePresence>{sug.length > 0 && <motion.div className="sugerencias" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                <div className="menu" style={{ position: "static" }}><div className="menu-t">Direcciones de este cliente</div>
                  {sug.map((d) => <button key={d} className="menu-i" onClick={() => { set("address", d); setSug([]); }}><MapPin />{d}</button>)}</div></motion.div>}</AnimatePresence>
            </div>
            <div className="campo"><label><Phone size={14} /> Teléfono</label><input className="inp" type="tel" value={est.phone} maxLength={40} placeholder="Ej: 099 123 456" onChange={(e) => set("phone", e.target.value)} autoComplete="off" /></div>
            <div className="campo"><label><Bike size={14} /> Repartidor</label>
              <select className="inp" value={est.courier_id} onChange={(e) => set("courier_id", e.target.value)}><option value="">Sin asignar</option>{reps.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
            <div className="campo ancho2"><label>Pago</label>
              <Seg full gr valor={est.payment_method} onChange={(v) => set("payment_method", v)} opciones={pagos.map((m) => [m.code, m.name, iconoPago(m), m.is_cash ? "verde" : "azul"])} /></div>
            <div className="campo"><label><StickyNote size={14} /> Nota</label><input className="inp" value={est.notes} maxLength={200} placeholder="Para cocina o reparto" onChange={(e) => set("notes", e.target.value)} /></div>
          </div>
        </div>

        <div className="vidrio catalogo">
          <div className="vidrio-h"><h3>Productos</h3><Tag>click para agregar</Tag></div>
          <div className="vidrio-b">
            <div className="catalogo-filtros">
              <div className="inp-ic"><Search /><input ref={busRef} className="inp" type="search" placeholder="Buscar producto o promo…  (F3)" value={busq} onChange={(e) => setBusq(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); if (promos[0]) agregarPromo(promos[0]); else if (lista[0]) agregar(lista[0].id); e.target.select(); } }} /></div>
              {(cats.length > 0 || boot.promos.length > 0) && <Seg valor={filtro} onChange={setFiltro} opciones={[["", "Todo"], ...(boot.promos.length ? [["__p", "Promos", Sparkles]] : []), ...cats.map((c) => [c, c])]} />}
            </div>
            <ClickSpark className="chispas" style={{ height: "auto" }} sparkColor={CHISPA} sparkCount={9} sparkRadius={26} sparkSize={9} duration={420}>
            <div ref={zonaBrillo} className="mosaico desvanece brillo-zona"><div className="mosaico-g">
              {promos.map((p) => <motion.button key={"p" + p.id} className="prod promo brillo" whileTap={{ scale: .95 }} onClick={() => agregarPromo(p)}>
                <Tag c="p" icono={Sparkles}>Promo</Tag><span className="p-n">{p.name}</span><span className="p-p grad-t">{plata(p.price_cents)}</span>
                <AnimatePresence>{promoCnt[p.id] && <motion.span key={promoCnt[p.id]} className="p-cnt" initial={{ scale: .4 }} animate={{ scale: 1 }} exit={{ scale: 0 }} transition={resorte}>{promoCnt[p.id]}</motion.span>}</AnimatePresence>
              </motion.button>)}
              {lista.map((p) => <motion.button key={p.id} className="prod brillo" whileTap={{ scale: .95 }} onClick={() => agregar(p.id)}>
                <span className="p-n">{p.name}</span><span className="p-p">{plata(p.price_cents)}</span>
                <AnimatePresence>{enPedido[p.id] && <motion.span key={enPedido[p.id]} className="p-cnt" initial={{ scale: .4 }} animate={{ scale: 1 }} exit={{ scale: 0 }} transition={resorte}>{enPedido[p.id]}</motion.span>}</AnimatePresence>
              </motion.button>)}
              {!promos.length && !lista.length && <div style={{ gridColumn: "1/-1" }}><Vacio titulo={boot.products.length ? "No hay productos que coincidan" : "Todavía no hay productos"} sub={boot.products.length ? "Probá con otra búsqueda." : "Cargalos en Productos y promos."} /></div>}
            </div></div>
            </ClickSpark>
          </div>
        </div>
      </div>

      <div className="vidrio pedido">
        <div className="vidrio-h">
          <div className="fila"><h3>Pedido actual</h3><Tag c="v">{unidades} {unidades === 1 ? "ítem" : "ítems"}</Tag></div>
          <Btn v="peligro" ch onClick={async () => { if (!est.items.length && !est.adjustments.length) return; if (await confirmar("¿Vaciar el pedido?", { ok: "Vaciar", peligro: true })) setEst((e) => ({ ...e, items: [], adjustments: [] })); }}>Vaciar</Btn>
        </div>
        <div className="pedido-l desvanece">
          <AnimatePresence initial={false}>
            {bloques.map((b) => b.promo ? (
              <motion.div layout key={b.grupo} className="linea promo" initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16, height: 0, marginTop: -9, padding: 0 }} transition={{ duration: .18 }}>
                <div className="l-top"><Tag c="p" icono={Sparkles}>Promo</Tag><span className="l-n">{b.nombre}</span><span className="l-p">{plata(b.precio + b.comps.reduce((a, i) => a + totalItem(boot, est.items[i]), 0))}</span>
                  <Btn v="fant" ico ch icono={X} title="Quitar promo" onClick={() => setEst((e) => ({ ...e, items: e.items.filter((x) => x.promo_group !== b.grupo) }))} /></div>
                {b.comps.map((i) => { const it = est.items[i], p = prods[it.product_id]; return (
                  <div key={i} className="comp"><div className="fila sb"><span className="small b7">{p ? p.name : "Producto"}</span><Btn v="fant" ch icono={Pencil} onClick={() => modificar(i)}>Modificar</Btn></div>{mods(it)}</div>); })}
              </motion.div>
            ) : (() => { const it = est.items[b.i], p = prods[it.product_id]; return (
              <motion.div layout key={`${it.product_id}-${b.i}`} className="linea" initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16 }} transition={{ duration: .18 }}>
                <div className="l-top"><span className="l-n">{p ? p.name : "Producto"}</span><span className="l-p">{plata(totalItem(boot, it))}</span></div>
                {mods(it)}
                <div className="l-ctrl">
                  <Paso valor={it.qty || 1} min={0} onChange={(n) => cambiarItem(b.i, (items) => { if (n <= 0) items.splice(b.i, 1); else items[b.i].qty = n; })} />
                  <Btn v="fant" ch icono={Pencil} onClick={() => modificar(b.i)}>Modificar</Btn><span className="crece" />
                  <Btn v="fant" ico ch icono={Trash2} title="Quitar" onClick={() => cambiarItem(b.i, (items) => items.splice(b.i, 1))} />
                </div>
              </motion.div>); })())}
          </AnimatePresence>
          {!bloques.length && <Vacio titulo="Todavía no agregaste productos" sub="Elegí uno a la izquierda." />}
        </div>
        <div className="pedido-pie">
          <div className="fila"><Btn bloque ch icono={Bike} onClick={() => ajuste("delivery")}>Cobrar envío</Btn><Btn bloque ch icono={Plus} onClick={() => ajuste("")}>Agregar ajuste</Btn></div>
          <AnimatePresence initial={false}>{est.adjustments.map((a, i) => { const c = parseInt(a.amount_cents, 10) || 0; return (
            <motion.div key={i + a.label} className="ajuste" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
              {a.kind === "delivery" && <Bike size={15} />}<span className="crece trunc">{a.label}</span><b className={`num ${c < 0 ? "mal-t" : "ok-t"}`}>{c < 0 ? "−" : "+"} {plata(Math.abs(c))}</b>
              <Btn v="fant" ico ch icono={X} onClick={() => setEst((e) => ({ ...e, adjustments: e.adjustments.filter((_, k) => k !== i) }))} />
            </motion.div>); })}</AnimatePresence>
          <div className="total"><span className="t-l">Total</span><span className="t-v"><Monto cents={total} /></span></div>
          {modo === "nuevo" ? <>
            <div className="fila sb"><span className="t2 small b7">Copias del ticket</span>
              <Paso valor={boot.settings.ticket_copies} min={1} max={10} onChange={(n) => { boot.settings.ticket_copies = n; setEst((e) => ({ ...e })); post("/api/config/preferencia", { ticket_copies: n }).catch(() => {}); }} /></div>
            <Btn v="prim" gr bloque icono={Printer} className={listo ? "listo" : ""} disabled={guardando} onClick={() => onGuardar(true)}>{guardando ? "Guardando…" : "Guardar + Imprimir"} <span className="kbd-cta">Ctrl+Enter</span><Haz /></Btn>
            <Btn v="fant" ch bloque disabled={guardando} onClick={() => onGuardar(false)}>Guardar sin imprimir</Btn>
          </> : <Btn v="prim" gr bloque icono={Check} className={listo ? "listo" : ""} disabled={guardando} onClick={() => onGuardar(false)}>{guardando ? "Guardando…" : "Guardar cambios"}<Haz /></Btn>}
        </div>
      </div>
    </div>
  );
}

/* ---------------- Modificar un ítem ---------------- */
function Modificar({ boot, est, setEst, indice, cerrar, arriba }) {
  const [i, setI] = useState(indice);
  const it = est.items[i] || {};
  const p = porId(boot.products)[it.product_id], ing = porId(boot.ingredients);
  // El estado del pedido vive afuera: leemos la versión fresca en cada render.
  const [, refrescar] = useState(0);
  const cambiar = (fn) => { setEst((e) => { const items = e.items.map((x) => ({ ...x, removed_ingredient_ids: [...(x.removed_ingredient_ids || [])], added_ingredient_ids: [...(x.added_ingredient_ids || [])] })); fn(items); Object.assign(est, { items }); return { ...e, items }; }); refrescar((n) => n + 1); };
  const base = (p ? p.base_ingredient_ids : []).map((id) => ing[id]).filter(Boolean);
  const extras = boot.ingredients.filter((x) => x.active || (it.added_ingredient_ids || []).includes(x.id));
  const cnt = (id) => (it.added_ingredient_ids || []).filter((x) => x === id).length;
  return (
    <Modal titulo={`Modificar · ${p ? p.name : "Producto"}`} tam="gr" onClose={() => cerrar()} arriba={arriba} pie={<Btn v="prim" icono={Check} onClick={() => cerrar()}>Listo</Btn>}>
      {(it.qty || 1) > 1 && <div className="aviso v mb"><Sparkles /><div className="crece">Los cambios se aplican a las <b>{it.qty} unidades</b> de esta línea.</div>
        <Btn ch onClick={() => cambiar((items) => { items[i].qty -= 1; items.splice(i + 1, 0, { product_id: it.product_id, qty: 1, removed_ingredient_ids: [...(it.removed_ingredient_ids || [])], added_ingredient_ids: [...(it.added_ingredient_ids || [])] }); setI(i + 1); })}>Separar 1 unidad</Btn></div>}
      <div className="grilla g2">
        <div><div className="etq mb">Quitar ingredientes</div>
          {base.length ? <div className="chips">{base.map((x) => { const on = (it.removed_ingredient_ids || []).includes(x.id); return (
            <button key={x.id} type="button" className={`chip quita ${on ? "on" : ""}`} onClick={() => cambiar((items) => { const r = items[i].removed_ingredient_ids; const k = r.indexOf(x.id); if (k >= 0) r.splice(k, 1); else r.push(x.id); })}>{on && <X />}{on ? "Sin " : ""}{x.name}</button>); })}</div>
            : <p className="t3 small">Este producto no tiene ingredientes base cargados.</p>}</div>
        <div><div className="etq mb">Agregar extras</div>
          {extras.length ? extras.map((x) => <div key={x.id} className="extra"><div className="crece"><b>{x.name}</b><div className="t3 small">{x.extra_price_cents ? "+ " + plata(x.extra_price_cents) : "sin cargo"}</div></div>
            <Paso valor={cnt(x.id)} min={0} max={20} onChange={(n) => cambiar((items) => { const a = items[i].added_ingredient_ids; if (n > cnt(x.id)) a.push(x.id); else { const k = a.indexOf(x.id); if (k >= 0) a.splice(k, 1); } })} /></div>)
            : <p className="t3 small">No hay extras cargados.</p>}</div>
      </div>
    </Modal>
  );
}

/* ---------------- Vista: Nuevo pedido ---------------- */
export default function Pos() {
  const [boot, setBoot] = useState(null);
  const [error, setError] = useState(null);
  const [est, setEst] = useState(() => { try { const g = JSON.parse(localStorage.getItem(GUARDADO) || "null"); if (g && Array.isArray(g.items)) return g; } catch (e) { /* nada */ } return null; });
  const [guardando, setGuardando] = useState(false);
  const refs = useRef({}).current;
  const cargar = useCallback(() => get("/api/bootstrap").then((b) => { setBoot(b); setEst((e) => e || nuevo((b.metodos_pago.find((m) => m.active) || {}).code)); }).catch(setError), []);
  useEffect(() => { cargar(); }, [cargar]);
  useEffect(() => { try { if (est && (est.items.length || est.address || est.phone)) localStorage.setItem(GUARDADO, JSON.stringify(est)); else localStorage.removeItem(GUARDADO); } catch (e) { /* nada */ } }, [est]);
  useEffect(() => { if (boot && refs.dir && refs.dir.current && !est.address) refs.dir.current.focus(); }, [boot]); // eslint-disable-line react-hooks/exhaustive-deps

  const guardar = useCallback(async (imprimir) => {
    if (guardando || !boot) return;
    const f = falta(est);
    if (f) { toast("Falta completar", f, "aviso"); if (!est.address.trim() && refs.dir.current) refs.dir.current.focus(); return; }
    setGuardando(true);
    try {
      const r = await post("/api/orders", cuerpoPedido(boot, est));
      if (imprimir) imprimirTicket(r.id, boot.settings.ticket_copies);
      toast(`Pedido N° ${r.id} guardado`, `${est.address} · ${plata(r.total_cents)}`, "ok");
      setEst(nuevo(est.payment_method));
      setTimeout(() => refs.dir.current && refs.dir.current.focus(), 50);
    } catch (e) {
      if (e.datos && e.datos.stale) { await cargar(); avisar(e.message, "Los precios cambiaron"); }
      else if (e.status !== 401) toast("No se pudo guardar el pedido", e.message, "error");
    } finally { setGuardando(false); }
  }, [boot, est, guardando, cargar, refs]);

  useEffect(() => {
    const k = (e) => {
      if (document.querySelector(".capa")) return;
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); guardar(true); }
      if (e.key === "F2") { e.preventDefault(); refs.dir.current && refs.dir.current.focus(); }
      if (e.key === "F3") { e.preventDefault(); refs.bus.current && refs.bus.current.focus(); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [guardar, refs]);

  if (error) return <div className="aviso r">{error.message}</div>;
  if (!boot || !est) return <div className="t3">Cargando…</div>;
  return <Editor boot={boot} est={est} setEst={setEst} modo="nuevo" onGuardar={guardar} guardando={guardando} refs={refs} />;
}

/* ---------------- Editar un pedido existente ---------------- */
export async function editarPedido(orderId, alGuardar) {
  const [boot, r] = await Promise.all([get("/api/bootstrap"), get(`/api/orders/${orderId}`)]);
  const promos = porId(boot.promos);
  const inicial = {
    phone: r.order.phone || "", address: r.order.address || "", courier_id: r.order.courier_id ? String(r.order.courier_id) : "",
    payment_method: r.order.payment_method || "cash", notes: r.order.notes || "",
    items: r.items.map((it) => ({ product_id: it.product_id, qty: it.qty || 1, removed_ingredient_ids: it.removed_ingredient_ids || [], added_ingredient_ids: it.added_ingredient_ids || [],
      ...(it.promo_group ? { promo_group: it.promo_group, promo_id: it.promo_id, promo_name: it.promo_name, promo_price_cents: it.promo_price_cents } : {}) })),
    adjustments: r.adjustments.map((a) => ({ label: a.label, amount_cents: a.amount_cents, kind: a.kind || "" })),
  };
  // El precio de la promo viene solo en el primer componente: se unifica (igual que el servidor).
  const precio = {};
  inicial.items.forEach((it) => { if (!it.promo_group) return; let p = (it.promo_id && promos[it.promo_id]) ? promos[it.promo_id].price_cents : 0; if (!p) p = it.promo_price_cents || 0; precio[it.promo_group] = Math.max(precio[it.promo_group] || 0, p); });
  inicial.items.forEach((it) => { if (it.promo_group) it.promo_price_cents = precio[it.promo_group] || 0; });

  return abrir(function EditarPedido({ cerrar, arriba }) {
    const [est, setEst] = useState(inicial);
    const [guardando, setGuardando] = useState(false);
    const guardar = async () => {
      const f = falta(est);
      if (f) return toast("Falta completar", f, "aviso");
      setGuardando(true);
      const body = cuerpoPedido(boot, est);
      try {
        try { await post(`/api/orders/${orderId}/update`, body); }
        catch (e) {
          if (!(e.datos && e.datos.needs_confirm)) throw e;
          if (!(await confirmar(e.message, { titulo: "Pedido entregado", ok: "Sí, modificar" }))) return;
          await post(`/api/orders/${orderId}/update`, { ...body, confirm_delivered: true });
        }
        toast(`Pedido N° ${orderId} actualizado`, "", "ok"); cerrar(true); if (alGuardar) alGuardar();
      } catch (e) {
        if (e.datos && e.datos.stale) avisar(e.message, "Los precios cambiaron");
        else if (e.status !== 401) toast("No se pudo guardar", e.message, "error");
      } finally { setGuardando(false); }
    };
    return (
      <Modal titulo={`Editar pedido N° ${orderId}`} tam="xl" onClose={() => cerrar(false)} arriba={arriba} sinPad>
        <div style={{ height: "min(80vh, 780px)", padding: 16 }}><Editor boot={boot} est={est} setEst={setEst} modo="editar" orderId={orderId} onGuardar={guardar} guardando={guardando} /></div>
      </Modal>
    );
  });
}
