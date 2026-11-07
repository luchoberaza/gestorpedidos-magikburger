/* Componentes de interfaz reutilizables. */
import React, { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, Check, CheckCircle2, Info, Inbox, Minus, Plus, X } from "lucide-react";
import { bus, plata, rango, textoPeriodo } from "./lib";
import { ValorAnimado } from "./efectos";
import StatusMark from "./rb/StatusMark";

export const resorte = { type: "spring", stiffness: 520, damping: 38 };

/* ---------------- Básicos ---------------- */
export function Btn({ v, ch, gr, ico, bloque, icono: I, children, className = "", ...p }) {
  const cls = ["btn", v, ch && "ch", gr && "gr", ico && "ico", bloque && "bloque", className].filter(Boolean).join(" ");
  return <button type="button" className={cls} {...p}>{I && <I />}{children}</button>;
}
export const Tag = ({ c, icono: I, children, ...p }) => <span className={`tag ${c || ""}`} {...p}>{I && <I />}{children}</span>;
export function Vidrio({ titulo, extra, children, pad, className = "", cuerpo = true, ...p }) {
  return (
    <div className={`vidrio ${pad ? "pad" : ""} ${className}`} {...p}>
      {titulo !== undefined && <div className="vidrio-h"><h3>{titulo}</h3>{extra}</div>}
      {titulo !== undefined && cuerpo ? <div className="vidrio-b">{children}</div> : children}
    </div>
  );
}
export function Kpi({ label, valor, sub, icono: I, c, grande, destacado, delta }) {
  return (
    <motion.div className={`kpi ${c || ""} ${grande ? "grande" : ""} ${destacado ? "destacado" : ""}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .25 }}>
      <div className="k-l">{I && <span className="k-i"><I /></span>}{label}{delta}</div>
      <div className="k-v"><ValorAnimado valor={valor} /></div>
      {sub && <div className="k-s">{sub}</div>}
    </motion.div>
  );
}
export function Delta({ actual, previo }) {
  if (!previo) return null;
  const p = Math.round(((actual - previo) / Math.abs(previo)) * 100);
  if (!isFinite(p) || p === 0) return null;
  return <span className={`delta ${p > 0 ? "up" : "down"}`} title="contra el día anterior">{p > 0 ? "▲" : "▼"} {Math.abs(p)}%</span>;
}
export function Vacio({ titulo, sub, icono: I = Inbox }) {
  return <div className="vacio"><div className="v-i"><I /></div><b>{titulo}</b>{sub && <span className="small">{sub}</span>}</div>;
}
export function Aviso({ c, icono, children }) {
  const I = icono || (c === "r" ? AlertTriangle : c === "a" ? AlertTriangle : c === "g" ? CheckCircle2 : Info);
  return <div className={`aviso ${c || ""}`}><I /><div className="crece">{children}</div></div>;
}
export function Campo({ label, ayuda, children, full, requerido }) {
  return (
    <div className={`campo ${full ? "full" : ""}`}>
      {label && <label>{label}{requerido && <span className="mal-t">*</span>}</label>}
      {children}
      {ayuda && <div className="ayuda">{ayuda}</div>}
    </div>
  );
}
export function Paso({ valor, onChange, min = 0, max = 99 }) {
  return (
    <div className="paso">
      <button type="button" disabled={valor <= min} onClick={() => onChange(valor - 1)}><Minus /></button>
      <span>{valor}</span>
      <button type="button" disabled={valor >= max} onClick={() => onChange(valor + 1)}><Plus /></button>
    </div>
  );
}
export const Interruptor = ({ checked, onChange }) => (
  <label className="sw"><input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)} /><i /></label>
);

/** Control segmentado con indicador que se desliza. opciones: [[valor, etiqueta, icono?, colorIndicador?]] */
export function Seg({ opciones, valor, onChange, full, gr, className = "" }) {
  const id = useId();
  return (
    <div className={`seg ${full ? "full" : ""} ${gr ? "gr" : ""} ${className}`}>
      {opciones.map(([v, t, I, color]) => (
        <button key={v} type="button" className={String(v) === String(valor) ? "on" : ""} onClick={() => onChange(v)}>
          {String(v) === String(valor) && <motion.span layoutId={`seg-${id}`} className={`seg-ind ${color || ""}`} transition={resorte} />}
          {I && <I />}<span>{t}</span>
        </button>
      ))}
    </div>
  );
}
export function Tabs({ tabs, valor, onChange }) {
  const id = useId();
  return (
    <div className="tabs">
      {tabs.map(([v, t, n]) => (
        <button key={v} type="button" className={`tab ${v === valor ? "on" : ""}`} onClick={() => onChange(v)}>
          {t}{n != null && <span className="n">{n}</span>}
          {v === valor && <motion.span layoutId={`tab-${id}`} className="tab-ind" transition={resorte} />}
        </button>
      ))}
    </div>
  );
}

/** Tabla: columnas [{t, k | v(fila, i), der, min}] */
export function Tabla({ columnas, filas, vacio = "Sin datos", vacioSub, pie, claveFila, apagada }) {
  return (
    <div className="tabla-w">
      <table className="tabla">
        <thead><tr>{columnas.map((c, i) => <th key={i} className={c.der ? "der" : ""}>{c.t}</th>)}</tr></thead>
        <tbody>
          {filas.length ? filas.map((f, i) => (
            <tr key={claveFila ? claveFila(f) : i} className={apagada && apagada(f) ? "apagado" : ""}>
              {columnas.map((c, j) => <td key={j} className={`${c.der ? "der" : ""} ${c.min ? "min" : ""}`}>{c.v ? c.v(f, i) : f[c.k]}</td>)}
            </tr>
          )) : <tr><td colSpan={columnas.length}><Vacio titulo={vacio} sub={vacioSub} /></td></tr>}
        </tbody>
        {pie && filas.length > 0 && <tfoot><tr>{pie.map((x, i) => <td key={i} className={columnas[i] && columnas[i].der ? "der" : ""}>{x}</td>)}</tr></tfoot>}
      </table>
    </div>
  );
}

/** Barra de período: hoy, semana, mes… y rango libre. */
export function BarraPeriodo({ valor, onChange, children }) {
  const ops = [["hoy", "Hoy"], ["ayer", "Ayer"], ["semana", "Semana"], ["mes", "Mes"], ["mesant", "Mes anterior"], ["anio", "Año"], ["todo", "Todo"], ["rango", "Rango"]];
  const elegir = (per) => onChange(per === "rango" ? { per, ...(valor.desde ? { desde: valor.desde, hasta: valor.hasta } : rango("mes")) } : { per, ...rango(per) });
  return (
    <div className="filtros">
      <Seg opciones={ops} valor={valor.per} onChange={elegir} />
      {valor.per === "rango" && (
        <div className="fila">
          <input className="inp" type="date" value={valor.desde} onChange={(e) => onChange({ ...valor, desde: e.target.value })} />
          <span className="t3">a</span>
          <input className="inp" type="date" value={valor.hasta} onChange={(e) => onChange({ ...valor, hasta: e.target.value })} />
        </div>
      )}
      {children}
    </div>
  );
}
export const textoDe = textoPeriodo;

/** Hook: carga datos de la API y permite recargar. */
export function useCarga(fn, deps) {
  const [estado, setEstado] = useState({ datos: null, error: null, cargando: true });
  const [n, setN] = useState(0);
  useEffect(() => {
    let vivo = true;
    setEstado((e) => ({ ...e, cargando: true }));
    Promise.resolve().then(fn).then((d) => vivo && setEstado({ datos: d, error: null, cargando: false }))
      .catch((e) => vivo && setEstado({ datos: null, error: e, cargando: false }));
    return () => { vivo = false; };
  }, [...(deps || []), n]); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...estado, recargar: () => setN((x) => x + 1) };
}
export function Cargando({ carga, children }) {
  if (carga.error && carga.error.status !== 401) return <Aviso c="r"><b>No se pudo cargar.</b> {carga.error.message}</Aviso>;
  if (!carga.datos) return <div className="col">{[0, 1, 2].map((i) => <div key={i} className="vidrio" style={{ height: 90, opacity: .5 - i * .12 }} />)}</div>;
  return children(carga.datos);
}

/* ---------------- Modales, confirmaciones y formularios ---------------- */
export function Modal({ titulo, tam, onClose, pie, children, arriba = true, extraH, sinPad }) {
  useEffect(() => {
    if (!arriba) return undefined;
    const k = (e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [arriba, onClose]);
  return (
    <motion.div className="capa" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: .15 }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <motion.div className={`modal ${tam || ""}`} role="dialog" aria-modal="true" initial={{ opacity: 0, y: 18, scale: .97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 10, scale: .98 }} transition={resorte}>
        <div className="modal-h"><h3>{titulo}</h3>{extraH}<Btn v="fant" ico ch icono={X} onClick={onClose} title="Cerrar (Esc)" /></div>
        <div className="modal-b" style={sinPad ? { padding: 0, overflow: "hidden" } : undefined}>{children}</div>
        {pie && <div className="modal-p">{pie}</div>}
      </motion.div>
    </motion.div>
  );
}

let sec = 0;
/** Abre un diálogo. Comp recibe {cerrar, arriba}. Devuelve una promesa con lo que pase a cerrar(). */
export function abrir(Comp) {
  return new Promise((res) => { const id = ++sec; bus.emit("dialogo+", { id, Comp, res }); });
}
export function DialogosHost() {
  const [pila, setPila] = useState([]);
  useEffect(() => bus.on("dialogo+", (d) => setPila((p) => [...p, d])), []);
  const cerrar = (id, valor) => setPila((p) => { const d = p.find((x) => x.id === id); if (d) d.res(valor); return p.filter((x) => x.id !== id); });
  return <AnimatePresence>{pila.map((d, i) => <d.Comp key={d.id} cerrar={(v) => cerrar(d.id, v)} arriba={i === pila.length - 1} />)}</AnimatePresence>;
}

export function confirmar(mensaje, o = {}) {
  return abrir(function Confirmar({ cerrar, arriba }) {
    const [checks, setChecks] = useState(() => Object.fromEntries((o.checks || []).map((c) => [c.name, c.valor !== false])));
    return (
      <Modal titulo={o.titulo || "Confirmar"} tam="ch" onClose={() => cerrar(false)} arriba={arriba}
        pie={<><Btn v="fant" onClick={() => cerrar(false)}>{o.cancelar || "Cancelar"}</Btn><Btn v={o.peligro ? "peligro" : "prim"} autoFocus onClick={() => cerrar({ si: true, ...checks })}>{o.ok || "Aceptar"}</Btn></>}>
        <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{mensaje}</p>
        {o.extra}
        {(o.checks || []).map((c) => <label key={c.name} className="chk mt"><input type="checkbox" checked={checks[c.name]} onChange={(e) => setChecks({ ...checks, [c.name]: e.target.checked })} />{c.label}</label>)}
      </Modal>
    );
  });
}
export function avisar(mensaje, titulo) {
  return abrir(({ cerrar, arriba }) => (
    <Modal titulo={titulo || "Aviso"} tam="ch" onClose={() => cerrar()} arriba={arriba} pie={<Btn v="prim" autoFocus onClick={() => cerrar()}>Entendido</Btn>}>
      <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{mensaje}</p>
    </Modal>
  ));
}

/**
 * Formulario en un modal.
 * campos: [{id, label, tipo: texto|numero|dinero|fecha|hora|select|textarea|check|clave|custom, valor, opciones, full, ayuda, placeholder, requerido, lista, render(valores, set)}]
 * guardar(datos) → si lanza, se muestra el error y el modal queda abierto.
 */
export function formulario(o) {
  return abrir(function Formulario({ cerrar, arriba }) {
    const [v, setV] = useState(() => Object.fromEntries(o.campos.filter((c) => c.id).map((c) => [c.id, c.valor == null ? (c.tipo === "check" ? false : "") : c.valor])));
    const [err, setErr] = useState("");
    const [mal, setMal] = useState(null);
    const [ocupado, setOcupado] = useState(false);
    const primero = useRef(null);
    useEffect(() => { if (primero.current && !o.sinFoco) { primero.current.focus(); if (o.seleccionar) primero.current.select(); } }, []);
    const set = (k, x) => setV((p) => ({ ...p, [k]: x }));
    const enviar = async () => {
      const falta = o.campos.find((c) => c.requerido && c.tipo !== "check" && !String(v[c.id] ?? "").trim());
      if (falta) { setErr(`Completá «${falta.label}».`); setMal(falta.id); return; }
      setOcupado(true);
      try { const r = await o.guardar(v); cerrar(r === undefined ? true : r); }
      catch (e) { setErr(e.message || String(e)); }
      finally { setOcupado(false); }
    };
    let foco = false;
    const ref = (c) => { if (!foco && c.tipo !== "check" && c.tipo !== "custom" && (!o.foco || o.foco === c.id)) { foco = true; return primero; } return undefined; };
    const visibles = o.campos.filter((c) => !c.mostrar || c.mostrar(v));
    return (
      <Modal titulo={o.titulo} tam={o.tam} onClose={() => cerrar(null)} arriba={arriba}
        pie={<>{o.pieExtra}<Btn v="fant" onClick={() => cerrar(null)}>Cancelar</Btn><Btn v="prim" disabled={ocupado} onClick={enviar}>{ocupado ? "Guardando…" : (o.ok || "Guardar")}</Btn></>}>
        {o.intro}
        <AnimatePresence>{err && <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}><Aviso c="r">{err}</Aviso><div style={{ height: 12 }} /></motion.div>}</AnimatePresence>
        <form className="form" onSubmit={(e) => { e.preventDefault(); enviar(); }} onKeyDown={(e) => { if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); enviar(); } }}>
          {visibles.map((c, i) => {
            const val = v[c.id];
            const cls = `inp ${mal === c.id ? "err" : ""}`;
            const on = (e) => { set(c.id, e.target.value); if (mal === c.id) setMal(null); };
            if (c.tipo === "custom") return <div key={c.id || i} className={c.full ? "full" : ""}>{c.render(v, set)}</div>;
            if (c.tipo === "check") return <label key={c.id} className={`chk ${c.full ? "full" : ""}`} style={c.full ? { gridColumn: "1/-1" } : undefined}><input type="checkbox" checked={!!val} onChange={(e) => set(c.id, e.target.checked)} />{c.label}</label>;
            let input;
            if (c.tipo === "select") input = <select ref={ref(c)} className={cls} value={val} onChange={on}>{(c.opciones || []).map((op) => { const [a, b] = Array.isArray(op) ? op : [op, op]; return <option key={a} value={a}>{b}</option>; })}</select>;
            else if (c.tipo === "textarea") input = <textarea ref={ref(c)} className={cls} rows={c.filas || 2} value={val} placeholder={c.placeholder} onChange={on} />;
            else {
              const tipo = { numero: "number", fecha: "date", hora: "time", clave: "password" }[c.tipo] || "text";
              input = <><input ref={ref(c)} className={cls} type={tipo} inputMode={c.tipo === "dinero" ? "decimal" : undefined} step={c.tipo === "numero" ? "any" : undefined}
                min={c.min} max={c.max} value={val} placeholder={c.placeholder || (c.tipo === "dinero" ? "0" : "")} onChange={on} list={c.lista ? `l-${c.id}` : undefined}
                autoComplete={c.tipo === "clave" ? "new-password" : "off"} maxLength={c.maxLen || 200} />
                {c.lista && <datalist id={`l-${c.id}`}>{c.lista.map((x) => <option key={x} value={x} />)}</datalist>}</>;
            }
            return <Campo key={c.id} label={c.label} ayuda={c.ayuda} full={c.full} requerido={c.requerido}>{input}</Campo>;
          })}
        </form>
      </Modal>
    );
  });
}

/* ---------------- Toasts ---------------- */
export function Toasts() {
  const [lista, setLista] = useState([]);
  useEffect(() => bus.on("toast", (t) => {
    setLista((l) => [...l.slice(-3), t]);
    setTimeout(() => setLista((l) => l.filter((x) => x.id !== t.id)), t.tipo === "error" ? 7000 : 3800);
  }), []);
  const ic = { aviso: AlertTriangle, info: Check };
  return (
    <div className="toasts">
      <AnimatePresence>
        {lista.map((t) => { const I = ic[t.tipo] || Info; return (
          <motion.div key={t.id} layout className={`toast ${t.tipo}`} initial={{ opacity: 0, y: 20, scale: .96 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 10, scale: .96 }} transition={resorte}
            onClick={() => setLista((l) => l.filter((x) => x.id !== t.id))}>
            <span className="t-i">{t.tipo === "ok" || t.tipo === "error" ? <TildeAnimada estado={t.tipo} /> : <I />}</span><div className="crece"><b>{t.titulo}</b>{t.detalle && <span>{t.detalle}</span>}</div>
          </motion.div>); })}
      </AnimatePresence>
    </div>
  );
}

/* Tilde que se dibuja (StatusMark de React Bits): arranca girando y se completa. */
function TildeAnimada({ estado }) {
  const [st, setSt] = useState("running");
  useEffect(() => { const t = setTimeout(() => setSt(estado === "ok" ? "done" : "failed"), 260); return () => clearTimeout(t); }, [estado]);
  return <StatusMark status={st} size={18} strokeWidth={2.2} color="currentColor" doneColor="currentColor" errorColor="currentColor" />;
}

/* ---------------- Menú desplegable ---------------- */
/* El resaltado sigue al mouse de una opción a otra (AnimatedBackground de Motion Primitives). */
function useResaltado(ref, abierto) {
  const [caja, setCaja] = useState(null);
  useEffect(() => {
    const el = ref.current;
    if (!abierto || !el) { setCaja(null); return undefined; }
    const sobre = (e) => {
      const it = e.target.closest && e.target.closest("button.menu-i");
      if (!it || !el.contains(it)) return;
      setCaja({ top: it.offsetTop, left: it.offsetLeft, width: it.offsetWidth, height: it.offsetHeight });
    };
    const fuera = () => setCaja(null);
    el.addEventListener("mouseover", sobre); el.addEventListener("mouseleave", fuera);
    return () => { el.removeEventListener("mouseover", sobre); el.removeEventListener("mouseleave", fuera); };
  }, [ref, abierto]);
  return caja;
}
export function Desplegable({ abierto, onClose, children, alinear = "derecha", ancho }) {
  const ref = useRef(null);
  const hl = useResaltado(ref, abierto);
  useEffect(() => {
    if (!abierto) return undefined;
    const f = (e) => { if (ref.current && !ref.current.parentElement.contains(e.target)) onClose(); };
    const k = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", f); window.addEventListener("keydown", k);
    return () => { document.removeEventListener("mousedown", f); window.removeEventListener("keydown", k); };
  }, [abierto, onClose]);
  return (
    <AnimatePresence>
      {abierto && <motion.div ref={ref} className="menu" style={{ top: "calc(100% + 8px)", [alinear === "derecha" ? "right" : "left"]: 0, minWidth: ancho }}
        initial={{ opacity: 0, y: -6, scale: .98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -4, scale: .98 }} transition={{ duration: .14 }}>
        <AnimatePresence>{hl && <motion.span className="menu-hl" style={{ inset: "auto", borderRadius: "var(--r-md)" }} initial={{ opacity: 0, ...hl }} animate={{ opacity: 1, ...hl }} exit={{ opacity: 0 }} transition={resorte} />}</AnimatePresence>
        {children}</motion.div>}
    </AnimatePresence>
  );
}

/* ---------------- Gráficos (SVG animado, sin librerías) ---------------- */
// Los colores de los gráficos salen de los tokens --c1…--c10 (estilos.css).
export const COLORES = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)", "var(--c7)", "var(--c8)", "var(--c9)", "var(--c10)"];
function useAncho(ref) {
  const [w, setW] = useState(600);
  useLayoutEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.floor(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}
function useTip() {
  const [t, setT] = useState(null);
  const el = t && <div className="tip" style={{ left: Math.min(window.innerWidth - 220, t.x + 14), top: t.y + 14 }} dangerouslySetInnerHTML={{ __html: t.html }} />;
  return [el, (e, html) => setT(html ? { x: e.clientX, y: e.clientY, html } : null)];
}
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const corto = (c) => { const p = c / 100, a = Math.abs(p); return (a >= 1e6 ? (p / 1e6).toFixed(1) + "M" : a >= 1e3 ? (p / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "k" : String(Math.round(p))).replace(".", ","); };
function escala(max) {
  if (max <= 0) return { top: 1, pasos: [0, 1] };
  const e = Math.pow(10, Math.floor(Math.log10(max)));
  const paso = [1, 2, 2.5, 5, 10].map((m) => m * e).find((s) => max / s <= 5) || e * 10;
  const top = Math.ceil(max / paso) * paso, pasos = [];
  for (let x = 0; x <= top + 1e-9; x += paso) pasos.push(x);
  return { top, pasos };
}
/** series: [{nombre, color, valores}] */
export function Barras({ etiquetas, series, alto = 230, fmt = plata, eje = corto }) {
  const ref = useRef(null), W = useAncho(ref), [tip, verTip] = useTip();
  const ml = 46, mb = 26, mt = 10, H = alto;
  const vals = series.flatMap((s) => s.valores.map((x) => Math.max(0, x)));
  const sc = escala(Math.max(1, ...vals));
  const y = (x) => H - mb - (Math.max(0, x) / sc.top) * (H - mt - mb);
  const n = etiquetas.length, gw = (W - ml - 6) / Math.max(1, n), bw = Math.max(3, Math.min(26, (gw * .7) / series.length));
  const cada = Math.max(1, Math.ceil(n / Math.floor((W - ml) / 48)));
  return (
    <div ref={ref}>
      <svg className="graf" width={W} height={H} onMouseLeave={() => verTip(null, null)}>
        {sc.pasos.map((p) => <g key={p}><line className="linea-g" x1={ml} x2={W - 6} y1={y(p)} y2={y(p)} /><text x={ml - 8} y={y(p) + 4} textAnchor="end">{eje(p)}</text></g>)}
        {etiquetas.map((et, i) => {
          const x0 = ml + gw * i + (gw - bw * series.length) / 2;
          return <g key={i}>
            {series.map((s, k) => { const v = s.valores[i] || 0, h = Math.max(v > 0 ? 2 : 0, H - mb - y(v)); return (
              <motion.rect key={k} x={x0 + k * bw} width={bw - 2} rx={4} fill={s.color} initial={{ height: 0, y: H - mb }} animate={{ height: h, y: H - mb - h }} transition={{ duration: .5, delay: i * .015 }}
                onMouseMove={(e) => verTip(e, `<b>${esc(et)}</b><br>${esc(s.nombre)}: ${esc(fmt(v))}`)} style={{ cursor: "default" }} />); })}
            {i % cada === 0 && <text x={ml + gw * i + gw / 2} y={H - 8} textAnchor="middle">{et}</text>}
          </g>;
        })}
      </svg>
      {series.length > 1 && <div className="leyenda">{series.map((s) => <span key={s.nombre}><i style={{ background: s.color }} />{s.nombre}</span>)}</div>}
      {tip}
    </div>
  );
}
export function Dona({ items, vacio = "Sin datos en el período" }) {
  const [tip, verTip] = useTip();
  const total = items.reduce((a, b) => a + b.valor, 0);
  if (!total) return <Vacio titulo={vacio} />;
  const R = 62, C = 2 * Math.PI * R;
  let off = 0;
  return (
    <div className="dona">
      <svg className="graf" viewBox="0 0 172 172" width="172" height="172" onMouseLeave={() => verTip(null, null)}>
        <circle r={R} cx="86" cy="86" fill="none" stroke="var(--glass-2)" strokeWidth="24" />
        {items.map((it, i) => { const f = it.valor / total, d = f * C, o = off; off += d; return (
          <motion.circle key={it.etiqueta} r={R} cx="86" cy="86" fill="none" stroke={COLORES[i % COLORES.length]} strokeWidth="24" transform="rotate(-90 86 86)"
            strokeDashoffset={-o} initial={{ strokeDasharray: `0 ${C}` }} animate={{ strokeDasharray: `${Math.max(0, d - 1.5)} ${C}` }} transition={{ duration: .6, delay: i * .05 }}
            onMouseMove={(e) => verTip(e, `<b>${esc(it.etiqueta)}</b><br>${esc(plata(it.valor))} · ${Math.round(f * 100)}%`)} />); })}
        <text x="86" y="80" textAnchor="middle">Total</text>
        <text x="86" y="100" textAnchor="middle" style={{ fontSize: 16, fontWeight: 800, fill: "var(--txt)" }}>{corto(total)}</text>
      </svg>
      <div className="col s">
        {items.slice(0, 8).map((it, i) => <div key={it.etiqueta} className="fila sb small"><span className="trunc"><i style={{ display: "inline-block", width: 10, height: 10, borderRadius: 3, background: COLORES[i % COLORES.length], marginRight: 8 }} />{it.etiqueta}</span><span className="num b7">{plata(it.valor)} <span className="t3">{Math.round((it.valor / total) * 100)}%</span></span></div>)}
      </div>
      {tip}
    </div>
  );
}
export function Ranking({ items, fmt = plata }) {
  if (!items.length) return <Vacio titulo="Sin datos en el período" />;
  const max = Math.max(1, ...items.map((i) => i.valor));
  return (
    <div className="ranking">
      {items.map((it, i) => <React.Fragment key={it.etiqueta}>
        <span className="trunc b7">{it.etiqueta} {it.extra && <span className="t3 small">{it.extra}</span>}</span>
        <span className="num b8">{fmt(it.valor)}</span>
        <div className="barra"><motion.i initial={{ width: 0 }} animate={{ width: `${Math.max(2, (it.valor / max) * 100)}%` }} transition={{ duration: .6, delay: i * .05 }} /></div>
      </React.Fragment>)}
    </div>
  );
}
