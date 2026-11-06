import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Bell, BarChart3, Boxes, Bike, ChevronDown, ClipboardList, KeyRound, LayoutDashboard, LogOut, Moon, Package, Receipt,
  Search, Settings, ShoppingBag, Sun, TrendingUp, Users, Wallet, ArrowRight, Repeat2, AlertTriangle,
} from "lucide-react";
import { bus, get, global, iniciales, post, puede, toast } from "./lib";
import { Btn, Desplegable, DialogosHost, Modal, Toasts, abrir, formulario, resorte } from "./ui";
import Pos from "./vistas/Pos";
import { Liquidacion, Pedidos } from "./vistas/Operacion";
import { Gastos, Inicio, Reportes, Ventas } from "./vistas/Finanzas";
import Inventario from "./vistas/Inventario";
import { Catalogo, Repartidores, Usuarios } from "./vistas/Admin";
import Config from "./vistas/Config";

export const VISTAS = [
  { id: "pos", titulo: "Nuevo pedido", corto: "Nuevo", icono: ShoppingBag, grupo: "caja", permiso: ["pos"], Comp: Pos, pos: true },
  { id: "pedidos", titulo: "Pedidos", icono: ClipboardList, grupo: "caja", permiso: ["pedidos"], Comp: Pedidos },
  { id: "liquidacion", titulo: "Liquidación", icono: Wallet, grupo: "caja", permiso: ["liquidacion"], Comp: Liquidacion },
  { id: "inicio", titulo: "Inicio", icono: LayoutDashboard, grupo: "negocio", permiso: ["inicio"], Comp: Inicio, desc: "Resumen del día y del mes" },
  { id: "ventas", titulo: "Ventas", icono: TrendingUp, grupo: "negocio", permiso: ["ventas"], Comp: Ventas, desc: "Pedidos y ventas manuales" },
  { id: "gastos", titulo: "Gastos", icono: Receipt, grupo: "negocio", permiso: ["gastos"], Comp: Gastos, desc: "Gastos y pagos fijos" },
  { id: "reportes", titulo: "Reportes", icono: BarChart3, grupo: "negocio", permiso: ["reportes"], Comp: Reportes, desc: "Ganancias y exportación" },
  { id: "inventario", titulo: "Inventario", icono: Boxes, grupo: "negocio", permiso: ["inventario"], Comp: Inventario, desc: "Stock, movimientos y recetas" },
  { id: "catalogo", titulo: "Productos y promos", icono: Package, grupo: "admin", permiso: ["catalogo"], Comp: Catalogo, desc: "Precios, promos y extras" },
  { id: "repartidores", titulo: "Repartidores", icono: Bike, grupo: "admin", permiso: ["repartidores"], Comp: Repartidores, desc: "Quién reparte y su cambio" },
  { id: "usuarios", titulo: "Usuarios", icono: Users, grupo: "admin", permiso: ["usuarios"], Comp: Usuarios, desc: "Accesos y permisos" },
  { id: "configuracion", titulo: "Configuración", icono: Settings, grupo: "admin", permiso: ["configuracion"], Comp: Config, desc: "Negocio, ticket, respaldo…" },
];
const GRUPOS = { negocio: "Negocio", admin: "Administración" };
const permitida = (v) => v.permiso.some(puede);

function aplicarTema(t) {
  document.documentElement.setAttribute("data-theme", t === "claro" ? "claro" : "oscuro");
  try { localStorage.setItem("mb_tema", t); } catch (e) { /* sin almacenamiento */ }
}

export default function App() {
  const [sesion, setSesion] = useState(null);
  const [estado, setEstado] = useState("cargando");
  const [mensajeLogin, setMensajeLogin] = useState("");

  const cargar = useCallback(async () => {
    for (let i = 0; i < 20; i++) {
      try {
        const s = await get("/api/sesion");
        global.sesion = s; setSesion(s); aplicarTema(s.negocio.tema); document.title = s.negocio.nombre;
        setEstado(s.usuario ? "app" : "login");
        return;
      } catch (e) { await new Promise((r) => setTimeout(r, 500)); }
    }
    setEstado("sin-conexion");
  }, []);
  useEffect(() => { cargar(); }, [cargar]);
  useEffect(() => bus.on("sin-sesion", (m) => { setMensajeLogin(m || ""); setEstado("login"); }), []);

  const entrar = (r) => {
    const s = { ...sesion, ...r, usuario: r.usuario };
    global.sesion = s; setSesion(s); setMensajeLogin(""); setEstado("app");
    try { localStorage.removeItem("mb_vista"); } catch (e) { /* nada */ }
  };
  const salir = async (mensaje) => { await post("/api/logout").catch(() => {}); setMensajeLogin(mensaje || ""); setEstado("login"); };

  return (
    <>
      {estado === "cargando" && <div className="login"><div className="t3">Cargando…</div></div>}
      {estado === "sin-conexion" && <div className="login"><div className="login-c"><h2>No se pudo conectar</h2><p className="t2">El servidor de la app no responde. Cerrá y volvé a abrir el programa.</p></div></div>}
      {estado === "login" && sesion && <Login sesion={sesion} mensaje={mensajeLogin} onEntrar={entrar} />}
      {estado === "app" && sesion && <Shell sesion={sesion} setSesion={(s) => { global.sesion = s; setSesion(s); }} onSalir={salir} />}
      <DialogosHost />
      <Toasts />
    </>
  );
}

/* ---------------- Login ---------------- */
function Login({ sesion, mensaje, onEntrar }) {
  const [usuarios, setUsuarios] = useState([]);
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState(mensaje);
  const [ocupado, setOcupado] = useState(false);
  const pass = useRef(null);
  useEffect(() => { get("/api/login/usuarios").then((r) => setUsuarios(r.usuarios)).catch(() => {}); }, []);
  const libre = sesion.acceso_libre;
  const entrar = async (usuario, clave) => {
    setOcupado(true); setErr("");
    try { onEntrar(await post("/api/login", { username: usuario, password: clave })); }
    catch (e) { setErr(e.message); if (pass.current) pass.current.select(); }
    finally { setOcupado(false); }
  };
  return (
    <div className="login">
      <motion.div className="login-c" initial={{ opacity: 0, y: 16, scale: .98 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={resorte}>
        <div className="marca-badge"><img src="/marca/logo" alt="" onError={(e) => { e.target.style.display = "none"; }} /></div>
        <h2>{sesion.negocio.nombre}</h2>
        <p className="t2" style={{ margin: "4px 0 20px" }}>Iniciá sesión para continuar</p>
        {usuarios.length > 0 && <>
          <div className="etq" style={{ marginBottom: 8 }}>¿Quién sos?</div>
          <div className="chips mb">{usuarios.map((x) => <button key={x.id} type="button" className={`chip ${u === x.username ? "on" : ""}`} onClick={() => { setU(x.username); setTimeout(() => pass.current && pass.current.focus(), 0); }}>{x.name}</button>)}</div>
        </>}
        <form className="col" onSubmit={(e) => { e.preventDefault(); if (u.trim()) entrar(u.trim(), p); }}>
          <div className="campo"><label>Usuario</label><input className="inp" autoFocus value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" /></div>
          <div className="campo"><label>Contraseña</label><input ref={pass} className="inp" type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" /></div>
          <AnimatePresence>{err && <motion.div className="mal-t small" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>{err}</motion.div>}</AnimatePresence>
          <Btn v="prim" gr bloque disabled={ocupado} type="submit" onClick={(e) => { e.preventDefault(); if (u.trim()) entrar(u.trim(), p); }}>{ocupado ? "Entrando…" : "Ingresar"}</Btn>
          {libre && <Btn v="fant" bloque onClick={() => { const x = usuarios.find((y) => y.id === libre.id); entrar(x ? x.username : "caja", ""); }}>Entrar como {libre.name} (sin contraseña)</Btn>}
        </form>
      </motion.div>
    </div>
  );
}

/* ---------------- Shell ---------------- */
function Shell({ sesion, setSesion, onSalir }) {
  const visibles = useMemo(() => VISTAS.filter(permitida), [sesion.usuario]); // eslint-disable-line react-hooks/exhaustive-deps
  const inicial = () => { let v = null; try { v = localStorage.getItem("mb_vista"); } catch (e) { /* nada */ } return visibles.find((x) => x.id === v) ? v : (visibles[0] && visibles[0].id); };
  const [vista, setVista] = useState(inicial);
  const [params, setParams] = useState({});
  const [menu, setMenu] = useState(null);
  const [paleta, setPaleta] = useState(false);
  const [avisos, setAvisos] = useState([]);
  const actual = visibles.find((v) => v.id === vista) || visibles[0];

  const ir = useCallback((id, p) => {
    if (!visibles.find((v) => v.id === id)) return;
    setVista(id); setParams(p || {}); setMenu(null);
    try { localStorage.setItem("mb_vista", id); } catch (e) { /* nada */ }
  }, [visibles]);

  const cargarAvisos = useCallback(() => get("/api/notificaciones").then((r) => setAvisos(r.avisos)).catch(() => {}), []);
  useEffect(() => { cargarAvisos(); const t = setInterval(cargarAvisos, 120000); return () => clearInterval(t); }, [cargarAvisos]);
  useEffect(() => bus.on("avisos", cargarAvisos), [cargarAvisos]);

  useEffect(() => {
    const k = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPaleta((x) => !x); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);

  useEffect(() => {
    if (sesion.clave_insegura) {
      const t = setTimeout(() => abrir(({ cerrar, arriba }) => (
        <Modal titulo="Cambiá la contraseña del administrador" tam="ch" onClose={() => cerrar()} arriba={arriba}
          pie={<><Btn v="fant" onClick={() => cerrar()}>Más tarde</Btn><Btn v="prim" onClick={() => { cerrar(); cambiarClave(sesion, setSesion); }}>Cambiarla ahora</Btn></>}>
          <div className="aviso a"><AlertTriangle /><div>Este usuario sigue con la contraseña de fábrica, que no es segura. Cambiala: la nueva la vas a saber solo vos.</div></div>
        </Modal>)), 500);
      return () => clearTimeout(t);
    }
    return undefined;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const u = sesion.usuario;
  const caja = visibles.filter((v) => v.grupo === "caja");
  const grupos = Object.keys(GRUPOS).map((g) => [g, visibles.filter((v) => v.grupo === g)]).filter(([, l]) => l.length);
  const tema = document.documentElement.getAttribute("data-theme");
  const cambiarTema = () => { const t = tema === "claro" ? "oscuro" : "claro"; aplicarTema(t); setSesion({ ...sesion, negocio: { ...sesion.negocio, tema: t } }); post("/api/config/tema", { tema: t }).catch(() => {}); };

  return (
    <div className="app">
      <header className="barra-sup">
        <div className="marca" onClick={() => visibles[0] && ir(visibles[0].id)} title="Ir al inicio">
          <div className="marca-badge"><img src="/marca/logo" alt="" onError={(e) => { e.target.replaceWith(document.createTextNode(iniciales(sesion.negocio.nombre))); }} /></div>
          <div><b>{sesion.negocio.nombre}</b><small>{actual ? actual.titulo : ""}</small></div>
        </div>

        <nav className="nav">
          {caja.map((v) => <NavBtn key={v.id} on={vista === v.id} icono={v.icono} onClick={() => ir(v.id)}>{v.corto || v.titulo}</NavBtn>)}
          {grupos.map(([g, lista]) => {
            const dentro = lista.find((v) => v.id === vista);
            return (
              <div key={g} className="rel">
                <NavBtn on={!!dentro} icono={dentro ? dentro.icono : (g === "admin" ? Settings : LayoutDashboard)} chev onClick={() => setMenu(menu === g ? null : g)}>{dentro ? dentro.titulo : GRUPOS[g]}</NavBtn>
                <Desplegable abierto={menu === g} onClose={() => setMenu(null)} ancho={280}>
                  <div className="menu-t">{GRUPOS[g]}</div>
                  {lista.map((v) => <button key={v.id} className={`menu-i ${vista === v.id ? "on" : ""}`} onClick={() => ir(v.id)}><v.icono /><div>{v.titulo}<small>{v.desc}</small></div></button>)}
                </Desplegable>
              </div>
            );
          })}
        </nav>

        <div className="top-acc">
          <button className="buscar-btn" onClick={() => setPaleta(true)} title="Buscar (Ctrl+K)"><Search /><span className="txt">Buscar</span><span className="kbd">Ctrl K</span></button>
          <div className="rel">
            <button className="ibtn" title="Avisos" onClick={() => { cargarAvisos(); setMenu(menu === "avisos" ? null : "avisos"); }}><Bell />{avisos.length > 0 && <span className="punto" />}</button>
            <Desplegable abierto={menu === "avisos"} onClose={() => setMenu(null)} ancho={340}>
              <div className="menu-t">Avisos</div>
              {avisos.length ? avisos.map((a, i) => (
                <button key={i} className="menu-i" onClick={() => ir(a.ir, a.tipo === "pago" ? { tab: "fijos" } : {})}>
                  <span className={`tag ${a.nivel === "alerta" ? "r" : "a"}`}>{a.tipo === "pago" ? "Pago" : a.tipo === "stock" ? "Stock" : "Pedidos"}</span>
                  <div>{a.titulo}<small>{a.detalle}</small></div>
                </button>)) : <div className="menu-i"><div><small>No hay avisos pendientes. Todo en orden.</small></div></div>}
            </Desplegable>
          </div>
          <div className="rel">
            <div className="usuario" onClick={() => setMenu(menu === "usuario" ? null : "usuario")}>
              <div className="avatar">{iniciales(u.name)}</div>
              <div><b>{u.name}</b><small>{u.role_label}{u.acceso_libre ? " · acceso libre" : ""}</small></div>
            </div>
            <Desplegable abierto={menu === "usuario"} onClose={() => setMenu(null)} ancho={250}>
              <div className="menu-t">{u.username}</div>
              <button className="menu-i" onClick={() => { setMenu(null); onSalir(""); }}><Repeat2 /><div>Cambiar de usuario</div></button>
              {u.tiene_clave && <button className="menu-i" onClick={() => { setMenu(null); cambiarClave(sesion, setSesion); }}><KeyRound /><div>Cambiar mi contraseña</div></button>}
              <button className="menu-i" onClick={() => { cambiarTema(); setMenu(null); }}>{tema === "claro" ? <Moon /> : <Sun />}<div>Tema {tema === "claro" ? "oscuro" : "claro"}</div></button>
              <div className="menu-sep" />
              <button className="menu-i" onClick={() => { setMenu(null); onSalir(""); }}><LogOut /><div>Cerrar sesión</div></button>
            </Desplegable>
          </div>
        </div>
      </header>

      <AnimatePresence mode="wait">
        <motion.main key={actual ? actual.id : "nada"} className={`cuerpo ${actual && actual.pos ? "pos" : ""}`}
          initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: .16 }}>
          {actual ? <actual.Comp ir={ir} params={params} vista={actual} /> : <div className="t3">Tu usuario no tiene secciones habilitadas. Pedile permisos a un administrador.</div>}
        </motion.main>
      </AnimatePresence>

      <AnimatePresence>{paleta && <Paleta visibles={visibles} ir={ir} cerrar={() => setPaleta(false)} />}</AnimatePresence>
    </div>
  );
}

function NavBtn({ on, icono: I, chev, children, onClick }) {
  return (
    <button type="button" className={`nav-btn ${on ? "on" : ""}`} onClick={onClick}>
      {on && <motion.span layoutId="nav-ind" className="nav-ind" transition={resorte} />}
      {I && <I />}<span className="lbl">{children}</span>{chev && <ChevronDown className="chev" />}
    </button>
  );
}

async function cambiarClave(sesion, setSesion) {
  const ok = await formulario({
    titulo: "Cambiar mi contraseña", tam: "ch",
    campos: [{ id: "actual", label: "Contraseña actual", tipo: "clave", full: true, requerido: true }, { id: "nueva", label: "Contraseña nueva", tipo: "clave", full: true, requerido: true }],
    guardar: async (d) => { await post("/api/mi-clave", d); },
  });
  if (ok) { toast("Contraseña cambiada", "", "ok"); setSesion({ ...global.sesion, clave_insegura: false }); }
}

/* ---------------- Paleta de comandos (Ctrl+K) ---------------- */
function Paleta({ visibles, ir, cerrar }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState([]);
  const [sel, setSel] = useState(0);
  useEffect(() => {
    const t = q.trim();
    if (t.length < 2) { setRes([]); return undefined; }
    const h = setTimeout(() => get("/api/buscar?q=" + encodeURIComponent(t)).then((r) => setRes(r.resultados)).catch(() => {}), 200);
    return () => clearTimeout(h);
  }, [q]);
  const navs = visibles.filter((v) => !q || v.titulo.toLowerCase().includes(q.toLowerCase())).map((v) => ({ tipo: "Ir a", titulo: v.titulo, detalle: v.desc || "", icono: v.icono, accion: () => ir(v.id) }));
  const encontrados = res.map((x) => ({ tipo: x.tipo, titulo: x.titulo, detalle: x.detalle, icono: ArrowRight, accion: () => ir(x.ir, { buscar: x }) }));
  const items = [...encontrados, ...navs];
  const elegir = (i) => { const it = items[i]; if (it) { cerrar(); it.accion(); } };
  return (
    <motion.div className="capa" style={{ placeItems: "start center" }} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={(e) => { if (e.target === e.currentTarget) cerrar(); }}>
      <motion.div className="paleta" initial={{ opacity: 0, y: -12, scale: .98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -8 }} transition={resorte}>
        <div className="paleta-in"><Search />
          <input autoFocus placeholder="Buscar pedidos, gastos, productos o ir a una sección…" value={q} onChange={(e) => { setQ(e.target.value); setSel(0); }}
            onKeyDown={(e) => { if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(items.length - 1, s + 1)); } if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); } if (e.key === "Enter") elegir(sel); if (e.key === "Escape") cerrar(); }} />
          <span className="kbd">Esc</span></div>
        <div className="paleta-l">
          {items.length ? items.map((it, i) => (
            <button key={i} className={`menu-i ${i === sel ? "sel" : ""}`} onMouseEnter={() => setSel(i)} onClick={() => elegir(i)}>
              <it.icono /><div className="crece">{it.titulo}<small>{it.tipo}{it.detalle ? ` · ${it.detalle}` : ""}</small></div>
            </button>)) : <div className="menu-i"><small>Sin resultados</small></div>}
        </div>
      </motion.div>
    </motion.div>
  );
}
