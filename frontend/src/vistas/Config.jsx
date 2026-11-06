import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Building2, Check, Clock, CreditCard, Database, Download, FileClock, Import, KeyRound, Pencil, Plus, Printer, Upload, Wallet, X } from "lucide-react";
import { aCentavos, api, descargar, fecha, get, global, hora, intentar, pesos, post, put, toast } from "../lib";
import { Aviso, Btn, Campo, Cargando, Tabla, Tag, Vidrio, avisar, confirmar, formulario, resorte, useCarga } from "../ui";
import { Encabezado } from "./Operacion";
import { limpiarOpciones } from "./Finanzas";

const SECCIONES = [
  ["negocio", "Negocio y marca", Building2], ["ticket", "Ticket e impresora", Printer], ["operacion", "Operación", Clock],
  ["pagos", "Medios de pago", CreditCard], ["finanzas", "Finanzas y stock", Wallet], ["acceso", "Acceso", KeyRound],
  ["respaldo", "Respaldo", Database], ["importar", "Importar de Caja", Import], ["auditoria", "Auditoría", FileClock],
];
let ultima = "negocio";

export default function Config() {
  const [sec, setSec] = useState(ultima);
  useEffect(() => { ultima = sec; }, [sec]);
  const carga = useCarga(() => get("/api/config"), []);
  return <>
    <Encabezado titulo="Configuración" sub="Todo lo que antes estaba fijo en el programa, ahora a mano" />
    <div className="cfg">
      <div className="vidrio cfg-nav">
        {SECCIONES.map(([k, t, I]) => <button key={k} className={sec === k ? "on" : ""} onClick={() => setSec(k)}>
          {sec === k && <motion.span layoutId="cfg-ind" className="cfg-ind" transition={resorte} />}<I /><span>{t}</span></button>)}
      </div>
      <Cargando carga={carga}>{(r) => <Seccion key={sec} sec={sec} r={r} recargar={carga.recargar} />}</Cargando>
    </div>
  </>;
}

function Seccion({ sec, r, recargar }) {
  const [v, setV] = useState(() => ({ ...r.config }));
  const [ocupado, setOcupado] = useState(false);
  const set = (k, x) => setV((p) => ({ ...p, [k]: x }));
  const guardar = async (claves) => {
    const d = {};
    claves.forEach((k) => { d[k] = v[k]; });
    setOcupado(true);
    const x = await intentar(() => post("/api/config", d), "No se guardó");
    setOcupado(false);
    if (!x) return;
    toast("Configuración guardada", "", "ok"); limpiarOpciones();
    if (global.sesion) {
      if (d.negocio_nombre) { global.sesion.negocio.nombre = d.negocio_nombre; document.title = d.negocio_nombre; }
      if (d.moneda_simbolo) global.sesion.negocio.simbolo = d.moneda_simbolo;
      if (d.hora_corte_jornada !== undefined) get("/api/sesion").then((s) => { global.sesion.hoy = s.hoy; }).catch(() => {});
    }
  };
  const Guardar = ({ claves }) => <div className="fila end mt"><Btn v="prim" icono={Check} disabled={ocupado} onClick={() => guardar(claves)}>Guardar cambios</Btn></div>;
  const texto = (k, label, ayuda) => <Campo label={label} ayuda={ayuda}><input className="inp" value={v[k] ?? ""} onChange={(e) => set(k, e.target.value)} /></Campo>;
  const num = (k, label, ayuda) => <Campo label={label} ayuda={ayuda}><input className="inp" type="number" value={v[k] ?? ""} onChange={(e) => set(k, e.target.value)} /></Campo>;
  const dinero = (k, label, ayuda) => <Campo label={label} ayuda={ayuda}><input className="inp" inputMode="decimal" defaultValue={pesos(v[k])} onChange={(e) => { const c = aCentavos(e.target.value); set(k, c == null ? 0 : c); }} /></Campo>;
  const check = (k, label) => <label className="chk"><input type="checkbox" checked={!!Number(v[k])} onChange={(e) => set(k, e.target.checked ? 1 : 0)} />{label}</label>;
  const anim = { initial: { opacity: 0, x: 10 }, animate: { opacity: 1, x: 0 }, transition: { duration: .18 } };

  if (sec === "negocio") return <motion.div className="col" {...anim}>
    <Vidrio titulo="Negocio"><div className="form">{texto("negocio_nombre", "Nombre del negocio")}{texto("moneda_simbolo", "Símbolo de moneda")}{texto("negocio_direccion", "Dirección del local")}{texto("negocio_telefono", "Teléfono del local")}</div>
      <Guardar claves={["negocio_nombre", "moneda_simbolo", "negocio_direccion", "negocio_telefono"]} /></Vidrio>
    <Logo propio={r.logo_propio} recargar={recargar} />
  </motion.div>;

  if (sec === "ticket") return <motion.div className="col" {...anim}>
    <Vidrio titulo="Ticket"><div className="form">{texto("ticket_encabezado", "Título del ticket", "Va arriba, grande. Vacío = sin título.")}{texto("ticket_pie", "Pie del ticket", "Ej: ¡Gracias por tu compra!")}
      {num("ticket_copies", "Copias por defecto")}<div />{check("ticket_logo", "Imprimir el logo")}{check("ticket_mostrar_telefono_local", "Mostrar el teléfono del local")}</div>
      <Guardar claves={["ticket_encabezado", "ticket_pie", "ticket_copies", "ticket_logo", "ticket_mostrar_telefono_local"]} /></Vidrio>
    <Impresora v={v} set={set} guardar={() => guardar(["printer_name"])} />
  </motion.div>;

  if (sec === "operacion") return <motion.div className="col" {...anim}>
    <Vidrio titulo="Jornada"><div className="form">{num("hora_corte_jornada", "Hora de corte de la jornada", "Un pedido tomado antes de esta hora cuenta para la noche anterior (6 = la jornada va de 06:00 a 05:59). 0 = día calendario.")}</div><Guardar claves={["hora_corte_jornada"]} /></Vidrio>
    <Vidrio titulo="Valores por defecto"><div className="form">{dinero("delivery_fee_cents", "Envío por defecto", "Se propone al tocar «Cobrar envío».")}{dinero("cambio_default_cents", "Cambio de un repartidor nuevo")}</div><Guardar claves={["delivery_fee_cents", "cambio_default_cents"]} /></Vidrio>
  </motion.div>;

  if (sec === "pagos") {
    const form = (x) => formulario({
      titulo: x ? `Editar ${x.name}` : "Nuevo medio de pago", tam: "ch",
      campos: [{ id: "name", label: "Nombre", valor: x ? x.name : "", full: true, requerido: true, placeholder: "Ej: Mercado Pago, Débito" },
        { id: "is_cash", label: "Es efectivo (lo cobra y lo rinde el repartidor)", tipo: "check", valor: x ? !!x.is_cash : false, full: true },
        { id: "hide_prices_ticket", label: "El ticket sale sin precios (ya está pagado)", tipo: "check", valor: x ? !!x.hide_prices_ticket : false, full: true },
        ...(x ? [{ id: "active", label: "Activo", tipo: "check", valor: !!x.active, full: true }, { id: "sort_order", label: "Orden", tipo: "numero", valor: x.sort_order }] : [])],
      guardar: async (d) => { if (x) await put(`/api/metodos-pago/${x.id}`, d); else await post("/api/metodos-pago", d); limpiarOpciones(); recargar(); },
    });
    return <motion.div {...anim}><Vidrio titulo="Medios de pago" extra={<Btn v="prim" ch icono={Plus} onClick={() => form(null)}>Nuevo</Btn>}>
      <p className="t2 small" style={{ marginTop: 0 }}>Los medios que no son efectivo no se rinden en la liquidación.</p>
      <Tabla filas={r.metodos_pago} apagada={(x) => !x.active} columnas={[{ t: "Nombre", v: (x) => <b>{x.name}</b> },
        { t: "¿Efectivo?", v: (x) => x.is_cash ? <Tag c="g">Sí · lo rinde el repartidor</Tag> : <Tag c="b">No · entra a la cuenta</Tag> },
        { t: "Ticket", v: (x) => <span className="small t2">{x.hide_prices_ticket ? "Sin precios (ya pagado)" : "Con precios"}</span> },
        { t: "Estado", v: (x) => x.active ? <Tag c="g">Activo</Tag> : <Tag>Inactivo</Tag> }, { t: "", min: true, v: (x) => <Btn ch icono={Pencil} onClick={() => form(x)} /> }]} />
    </Vidrio></motion.div>;
  }

  if (sec === "finanzas") return <motion.div className="col" {...anim}>
    <Vidrio titulo="Gastos"><div className="form">{dinero("umbral_gasto_cents", "Avisar si un gasto supera", "0 = no avisar.")}{num("recordatorio_dias_aviso", "Avisar pagos fijos con estos días de anticipación")}
      <Campo label="Categorías de gasto" full><Etiquetas valor={v.categorias_gasto} onChange={(x) => set("categorias_gasto", x)} /></Campo></div>
      <Guardar claves={["umbral_gasto_cents", "recordatorio_dias_aviso", "categorias_gasto"]} /></Vidrio>
    <Vidrio titulo="Stock"><Campo label="Unidades de medida"><Etiquetas valor={v.unidades_stock} onChange={(x) => set("unidades_stock", x)} /></Campo><Guardar claves={["unidades_stock"]} /></Vidrio>
  </motion.div>;

  if (sec === "acceso") return <motion.div {...anim}><Vidrio titulo="Acceso libre a la caja">
    <Campo label="Al abrir la app, entrar automáticamente como" ayuda="Así la caja toma pedidos sin contraseña, como siempre. Para administrar hay que cambiar a un usuario con permisos.">
      <select className="inp" value={v.acceso_libre_usuario_id} onChange={(e) => set("acceso_libre_usuario_id", e.target.value)}>
        <option value="">Nadie: pedir usuario y contraseña siempre</option>{r.usuarios.map((u) => <option key={u.id} value={String(u.id)}>{u.name}</option>)}</select></Campo>
    <Guardar claves={["acceso_libre_usuario_id"]} /></Vidrio></motion.div>;

  if (sec === "respaldo") return <motion.div className="col" {...anim}><Respaldo r={r} v={v} set={set} guardar={guardar} /></motion.div>;
  if (sec === "importar") return <motion.div {...anim}><Importar /></motion.div>;
  if (sec === "auditoria") return <motion.div {...anim}><Auditoria /></motion.div>;
  return null;
}

function Etiquetas({ valor, onChange }) {
  const [t, setT] = useState("");
  return <div className="etiquetas">
    {valor.map((x) => <span key={x} className="chip">{x}<button type="button" onClick={() => onChange(valor.filter((y) => y !== x))}><X size={14} /></button></span>)}
    <input value={t} placeholder="Agregar y Enter…" onChange={(e) => setT(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && t.trim()) { e.preventDefault(); if (!valor.some((y) => y.toLowerCase() === t.trim().toLowerCase())) onChange([...valor, t.trim()]); setT(""); } }} />
  </div>;
}

function Logo({ propio, recargar }) {
  const [n, setN] = useState(Date.now());
  const refrescar = () => { setN(Date.now()); document.querySelectorAll("img[src^='/marca/logo']").forEach((i) => { i.src = "/marca/logo?" + Date.now(); }); recargar(); };
  const subir = async (f) => { const fd = new FormData(); fd.append("logo", f); if (await intentar(() => api("/api/config/logo", { method: "POST", form: fd }), "No se pudo subir el logo")) { toast("Logo actualizado", "", "ok"); refrescar(); } };
  return <Vidrio titulo="Logo"><div className="fila">
    <img className="logo-prev" src={`/marca/logo?${n}`} alt="" />
    <div className="col s"><span className="t2 small">Se usa en la barra, el ingreso, el PDF del cierre y el ticket. PNG o JPG cuadrado.</span>
      <div className="fila"><label className="btn ch"><Upload size={15} />Subir logo<input type="file" accept=".png,.jpg,.jpeg,.webp" hidden onChange={(e) => e.target.files[0] && subir(e.target.files[0])} /></label>
        {propio && <Btn v="fant" ch onClick={async () => { if (await intentar(() => api("/api/config/logo", { method: "DELETE" }))) refrescar(); }}>Volver al de fábrica</Btn>}</div></div>
  </div></Vidrio>;
}

function Impresora({ v, set, guardar }) {
  const [imp, setImp] = useState(null);
  useEffect(() => { get("/api/impresoras").then(setImp).catch(() => setImp({ impresoras: [], predeterminada: null })); }, []);
  const lista = imp ? [...new Set([...(v.printer_name ? [v.printer_name] : []), ...imp.impresoras])] : [];
  return <Vidrio titulo="Impresora térmica">
    <p className="t2 small" style={{ marginTop: 0 }}>La impresión va directa (ESC/POS): nítida y sin diálogos. Si falla, se abre la impresión común de Windows.</p>
    <Campo label="Impresora" ayuda={imp ? (imp.predeterminada ? `Predeterminada de Windows: ${imp.predeterminada}` : (imp.impresoras.length ? "" : "No se detectaron impresoras (solo funciona en Windows).")) : "Buscando impresoras…"}>
      <select className="inp" value={v.printer_name} onChange={(e) => set("printer_name", e.target.value)}><option value="">Usar la predeterminada de Windows</option>{lista.map((p) => <option key={p}>{p}</option>)}</select></Campo>
    <div className="fila end mt"><Btn icono={Printer} onClick={async () => { const x = await intentar(() => post("/api/impresoras/prueba", { printer: v.printer_name }), "La prueba falló"); if (x) toast("Prueba enviada", x.printer, "ok"); }}>Imprimir prueba</Btn>
      <Btn v="prim" icono={Check} onClick={guardar}>Guardar</Btn></div>
  </Vidrio>;
}

function Respaldo({ r, v, set, guardar }) {
  const lista = useCarga(() => get("/api/respaldo/lista"), []);
  const restaurar = async (f) => {
    if (!(await confirmar(`Se van a reemplazar TODOS los datos actuales por los de «${f.name}».\nAntes se guarda un respaldo automático de lo actual.`, { titulo: "Restaurar respaldo", ok: "Restaurar", peligro: true }))) return;
    const fd = new FormData(); fd.append("archivo", f);
    if (await intentar(() => api("/api/respaldo/restaurar", { method: "POST", form: fd }), "No se pudo restaurar")) { await avisar("Datos restaurados. La app se va a recargar.", "Listo"); window.location.reload(); }
  };
  return <>
    <Vidrio titulo="Copia de seguridad">
      <p className="t2 small" style={{ marginTop: 0 }}>Todo (pedidos, ventas, gastos, stock, usuarios y configuración) en un solo archivo .db. Guardalo en un pendrive o en la nube.</p>
      <div className="fila wrap"><Btn v="prim" icono={Download} onClick={() => descargar("/api/respaldo/descargar")}>Descargar respaldo ahora</Btn>
        <label className="btn"><Upload size={17} />Restaurar desde archivo<input type="file" accept=".db" hidden onChange={(e) => { const f = e.target.files[0]; e.target.value = ""; if (f) restaurar(f); }} /></label></div>
      <div className="t3 small mt">Carpeta de datos: {r.carpeta_datos}</div>
    </Vidrio>
    <Vidrio titulo="Respaldos automáticos">
      <div className="form"><label className="chk"><input type="checkbox" checked={!!Number(v.respaldo_auto)} onChange={(e) => set("respaldo_auto", e.target.checked ? 1 : 0)} />Respaldo automático (al abrir, una vez por día, y en cada cierre)</label>
        <Campo label="Cantidad a conservar"><input className="inp" type="number" value={v.respaldos_a_conservar} onChange={(e) => set("respaldos_a_conservar", e.target.value)} /></Campo></div>
      <div className="fila end mt mb"><Btn v="prim" icono={Check} onClick={() => guardar(["respaldo_auto", "respaldos_a_conservar"])}>Guardar cambios</Btn></div>
      <Cargando carga={lista}>{(l) => <Tabla filas={l.respaldos} vacio="Todavía no hay respaldos automáticos"
        columnas={[{ t: "Archivo", k: "nombre" }, { t: "Tamaño", der: true, v: (x) => `${(x.bytes / 1024 / 1024).toFixed(1)} MB` }, { t: "", min: true, v: (x) => <Btn ch icono={Download} onClick={() => descargar("/api/respaldo/archivo/" + encodeURIComponent(x.nombre))} /> }]} />}</Cargando>
    </Vidrio>
  </>;
}

function Importar() {
  const [res, setRes] = useState(null);
  const [ocupado, setOcupado] = useState(false);
  const subir = async (f) => {
    const fd = new FormData(); fd.append("archivo", f);
    setOcupado(true); setRes(null);
    const x = await intentar(() => api("/api/importar/caja", { method: "POST", form: fd }), "No se pudo importar");
    setOcupado(false);
    if (x) { setRes(x.resultado); limpiarOpciones(); }
  };
  return <Vidrio titulo="Importar datos de «Caja Hamburguesería»">
    <div className="col">
      <p className="t2" style={{ margin: 0 }}>Trae gastos, ventas, inventario (con sus movimientos), recordatorios de pago y usuarios del programa anterior. Se puede repetir: lo que ya se importó no se duplica.</p>
      <Aviso c="v">El archivo se llama <b>caja.db</b> y está en <code>%APPDATA%\caja-hamburgueseria\data</code> de la PC donde se usaba Caja (pegá esa ruta en el Explorador de Windows).</Aviso>
      <div><label className="btn prim"><Upload size={17} />{ocupado ? "Importando…" : "Elegir caja.db e importar"}<input type="file" accept=".db" hidden disabled={ocupado} onChange={(e) => { const f = e.target.files[0]; e.target.value = ""; if (f) subir(f); }} /></label></div>
      {res && <Aviso c="g"><b>Importación terminada.</b><br />{res.gastos} gasto(s), {res.ventas} venta(s), {res.insumos} insumo(s), {res.movimientos} movimiento(s), {res.recordatorios} recordatorio(s), {res.usuarios} usuario(s).
        {res.omitidos > 0 && <><br /><span className="small">{res.omitidos} registro(s) ya estaban y no se duplicaron.</span></>}</Aviso>}
    </div>
  </Vidrio>;
}

function Auditoria() {
  const carga = useCarga(() => get("/api/auditoria"), []);
  return <Vidrio titulo="Auditoría" extra={<span className="t3 small">Pedidos eliminados o editados, cierres, precios, usuarios…</span>}>
    <Cargando carga={carga}>{(a) => <Tabla filas={a.eventos} vacio="Sin eventos registrados"
      columnas={[{ t: "Fecha", v: (x) => <>{fecha(x.fecha)} <span className="t3 small">{hora(x.fecha)}</span></> }, { t: "Usuario", v: (x) => x.user_name || "—" }, { t: "Acción", v: (x) => <b>{x.accion}</b> }, { t: "Detalle", v: (x) => <span className="small t2">{x.detalle}</span> }]} />}</Cargando>
  </Vidrio>;
}
