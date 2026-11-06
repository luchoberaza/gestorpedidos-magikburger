import { useState } from "react";
import { Pencil, Plus, Sparkles, Trash2 } from "lucide-react";
import { del, fechaHora, get, iniciales, intentar, pesos, plata, post, put, toast } from "../lib";
import { Btn, Cargando, Paso, Tabla, Tabs, Tag, confirmar, formulario, useCarga } from "../ui";
import { Encabezado } from "./Operacion";

const borrarCon = async (msg, url, recargar) => {
  if (!(await confirmar(msg, { ok: "Eliminar", peligro: true }))) return;
  const x = await intentar(() => del(url));
  if (x) { toast(x.mensaje || "Eliminado", "", "ok"); recargar(); }
};
const Acciones = ({ editar, borrar }) => <div className="acc"><Btn icono={Pencil} title="Editar" onClick={editar} /><Btn className="del" icono={Trash2} title="Eliminar" onClick={borrar} /></div>;

/* ---------------- Catálogo ---------------- */
export function Catalogo() {
  const [tab, setTab] = useState("productos");
  const carga = useCarga(() => get("/api/admin/catalogo"), []);
  return <>
    <Encabezado titulo="Productos y promos" sub="Los precios nuevos rigen desde el próximo pedido; los ya tomados conservan el suyo." />
    <Cargando carga={carga}>{(r) => {
      const ing = Object.fromEntries(r.ingredientes.map((i) => [i.id, i]));
      const prod = Object.fromEntries(r.productos.map((p) => [p.id, p]));
      return <>
        <Tabs valor={tab} onChange={setTab} tabs={[["productos", "Productos", r.productos.length], ["promos", "Promos", r.promos.length], ["ingredientes", "Ingredientes y extras", r.ingredientes.length]]} />
        {tab === "productos" && <Productos r={r} ing={ing} recargar={carga.recargar} />}
        {tab === "promos" && <Promos r={r} prod={prod} recargar={carga.recargar} />}
        {tab === "ingredientes" && <Ingredientes r={r} recargar={carga.recargar} />}
      </>;
    }}</Cargando>
  </>;
}

function Productos({ r, ing, recargar }) {
  const form = (p) => formulario({
    titulo: p ? `Editar ${p.name}` : "Nuevo producto", tam: "gr",
    campos: [{ id: "name", label: "Nombre", valor: p ? p.name : "", requerido: true, placeholder: "Ej: Doble cheddar" },
      { id: "price", label: "Precio", tipo: "dinero", valor: p ? pesos(p.price_cents) : "", requerido: true },
      { id: "category", label: "Categoría (agrupa en la caja)", valor: p ? p.category : "", lista: r.categorias, placeholder: "Ej: Hamburguesas, Bebidas" },
      { id: "active", label: "Activo (aparece en la caja)", tipo: "check", valor: p ? !!p.active : true },
      { id: "base_ingredient_ids", tipo: "custom", full: true, valor: p ? p.base_ingredient_ids : [], render: (v, set) => <>
        <div className="etq" style={{ marginBottom: 8 }}>Ingredientes base <span className="t3">(los que se pueden quitar: "sin cebolla")</span></div>
        <div className="chips">{r.ingredientes.filter((i) => i.active || v.base_ingredient_ids.includes(i.id)).map((i) => { const on = v.base_ingredient_ids.includes(i.id); return (
          <button key={i.id} type="button" className={`chip ${on ? "on" : ""}`} onClick={() => set("base_ingredient_ids", on ? v.base_ingredient_ids.filter((x) => x !== i.id) : [...v.base_ingredient_ids, i.id])}>{i.name}</button>); })}
          {!r.ingredientes.length && <span className="t3 small">Primero cargá ingredientes en «Ingredientes y extras».</span>}</div></> }],
    guardar: async (d) => { if (p) await put(`/api/admin/productos/${p.id}`, d); else await post("/api/admin/productos", d); toast(p ? "Producto actualizado" : "Producto creado", d.name, "ok"); recargar(); },
  });
  return <>
    <div className="filtros"><span className="crece" /><Btn v="prim" icono={Plus} onClick={() => form(null)}>Nuevo producto</Btn></div>
    <Tabla filas={r.productos} apagada={(p) => !p.active} vacio="No hay productos" vacioSub="Creá el primero con «Nuevo producto»."
      columnas={[{ t: "Producto", v: (p) => <b>{p.name}</b> }, { t: "Categoría", v: (p) => p.category ? <Tag>{p.category}</Tag> : <span className="t3">—</span> },
        { t: "Ingredientes base", v: (p) => p.base_ingredient_ids.length ? <span className="small t2">{p.base_ingredient_ids.map((id) => ing[id] && ing[id].name).filter(Boolean).join(", ")}</span> : <span className="t3">—</span> },
        { t: "Precio", der: true, v: (p) => <b>{plata(p.price_cents)}</b> }, { t: "Estado", v: (p) => p.active ? <Tag c="g">Activo</Tag> : <Tag>Inactivo</Tag> },
        { t: "", min: true, v: (p) => <Acciones editar={() => form(p)} borrar={() => borrarCon(`¿Eliminar «${p.name}»?${p.usado ? "\nTiene pedidos: queda desactivado para no perder el historial." : ""}`, `/api/admin/productos/${p.id}`, recargar)} /> }]} />
  </>;
}

function Promos({ r, prod, recargar }) {
  const form = (p) => {
    const inicial = {}; (p ? p.component_product_ids : []).forEach((id) => { inicial[id] = (inicial[id] || 0) + 1; });
    return formulario({
      titulo: p ? `Editar ${p.name}` : "Nueva promo", tam: "gr",
      campos: [{ id: "name", label: "Nombre", valor: p ? p.name : "", requerido: true, placeholder: "Ej: Promo pareja" }, { id: "price", label: "Precio de la promo", tipo: "dinero", valor: p ? pesos(p.price_cents) : "", requerido: true },
        { id: "active", label: "Activa (aparece en la caja)", tipo: "check", valor: p ? !!p.active : true, full: true },
        { id: "cant", tipo: "custom", full: true, valor: inicial, render: (v, set) => { const suelto = Object.entries(v.cant).reduce((a, [id, k]) => a + (prod[id] ? prod[id].price_cents * k : 0), 0); return <>
          <div className="etq" style={{ marginBottom: 8 }}>Productos que incluye</div>
          {r.productos.filter((x) => x.active || v.cant[x.id]).map((x) => <div key={x.id} className="extra"><div className="crece"><b>{x.name}</b><div className="t3 small">{plata(x.price_cents)} suelto</div></div>
            <Paso valor={v.cant[x.id] || 0} min={0} max={20} onChange={(n) => set("cant", { ...v.cant, [x.id]: n })} /></div>)}
          {suelto > 0 && <div className="small t2 mt-s">Comprado suelto saldría {plata(suelto)}.</div>}</>; } }],
      guardar: async (d) => {
        const body = { ...d, component_product_ids: Object.entries(d.cant).flatMap(([id, k]) => Array(k).fill(+id)) };
        if (p) await put(`/api/admin/promos/${p.id}`, body); else await post("/api/admin/promos", body);
        toast(p ? "Promo actualizada" : "Promo creada", d.name, "ok"); recargar();
      },
    });
  };
  return <>
    <div className="filtros"><span className="t2 small">Combos con precio propio. Los extras de cada componente se cobran aparte.</span><span className="crece" /><Btn v="prim" icono={Plus} onClick={() => form(null)}>Nueva promo</Btn></div>
    <Tabla filas={r.promos} apagada={(p) => !p.active} vacio="No hay promos" vacioSub="Armá combos con productos ya cargados."
      columnas={[{ t: "Promo", v: (p) => <span className="fila"><Sparkles size={16} className="vi-t" /><b>{p.name}</b></span> },
        { t: "Incluye", v: (p) => { const c = {}; p.component_product_ids.forEach((id) => { c[id] = (c[id] || 0) + 1; }); return <div className="chips">{Object.entries(c).map(([id, k]) => <Tag key={id} c="p">{k > 1 ? `${k}× ` : ""}{prod[id] ? prod[id].name : "?"}</Tag>)}</div>; } },
        { t: "Precio", der: true, v: (p) => <b>{plata(p.price_cents)}</b> }, { t: "Estado", v: (p) => p.active ? <Tag c="g">Activa</Tag> : <Tag>Pausada</Tag> },
        { t: "", min: true, v: (p) => <Acciones editar={() => form(p)} borrar={() => borrarCon(`¿Eliminar la promo «${p.name}»?`, `/api/admin/promos/${p.id}`, recargar)} /> }]} />
  </>;
}

function Ingredientes({ r, recargar }) {
  const form = (i) => formulario({
    titulo: i ? `Editar ${i.name}` : "Nuevo ingrediente", tam: "ch",
    campos: [{ id: "name", label: "Nombre", valor: i ? i.name : "", full: true, requerido: true }, { id: "extra_price", label: "Precio como extra", tipo: "dinero", valor: i ? pesos(i.extra_price_cents) : "", full: true, ayuda: "0 si no se cobra." },
      { id: "active", label: "Activo", tipo: "check", valor: i ? !!i.active : true }],
    guardar: async (d) => { if (i) await put(`/api/admin/ingredientes/${i.id}`, d); else await post("/api/admin/ingredientes", d); toast("Guardado", d.name, "ok"); recargar(); },
  });
  return <>
    <div className="filtros"><span className="t2 small">Se usan como base (para quitar) y como extras con precio (para agregar).</span><span className="crece" /><Btn v="prim" icono={Plus} onClick={() => form(null)}>Nuevo ingrediente</Btn></div>
    <Tabla filas={r.ingredientes} apagada={(i) => !i.active} vacio="No hay ingredientes"
      columnas={[{ t: "Ingrediente", v: (i) => <b>{i.name}</b> }, { t: "Precio como extra", der: true, v: (i) => i.extra_price_cents ? plata(i.extra_price_cents) : <span className="t3">sin cargo</span> },
        { t: "Estado", v: (i) => i.active ? <Tag c="g">Activo</Tag> : <Tag>Inactivo</Tag> },
        { t: "", min: true, v: (i) => <Acciones editar={() => form(i)} borrar={() => borrarCon(`¿Eliminar «${i.name}»?`, `/api/admin/ingredientes/${i.id}`, recargar)} /> }]} />
  </>;
}

/* ---------------- Repartidores ---------------- */
export function Repartidores() {
  const carga = useCarga(() => get("/api/admin/repartidores"), []);
  return <>
    <Encabezado titulo="Repartidores" sub="El cambio es lo que la casa le da para vuelto: se suma a lo que rinde al cierre." />
    <Cargando carga={carga}>{(r) => {
      const form = (x) => formulario({
        titulo: x ? `Editar ${x.name}` : "Nuevo repartidor", tam: "ch",
        campos: [{ id: "name", label: "Nombre", valor: x ? x.name : "", full: true, requerido: true }, { id: "cash_float", label: "Cambio que lleva", tipo: "dinero", full: true, valor: pesos(x ? x.cash_float_cents : r.cambio_default_cents) },
          { id: "active", label: "Activo (aparece en la caja)", tipo: "check", valor: x ? !!x.active : true }],
        guardar: async (d) => { if (x) await put(`/api/admin/repartidores/${x.id}`, d); else await post("/api/admin/repartidores", d); toast("Repartidor guardado", d.name, "ok"); carga.recargar(); },
      });
      return <>
        <div className="filtros"><span className="crece" /><Btn v="prim" icono={Plus} onClick={() => form(null)}>Nuevo repartidor</Btn></div>
        <Tabla filas={r.repartidores} apagada={(x) => !x.active} vacio="No hay repartidores"
          columnas={[{ t: "Repartidor", v: (x) => <div className="fila"><div className="avatar">{iniciales(x.name)}</div><b>{x.name}</b></div> }, { t: "Cambio que lleva", der: true, v: (x) => plata(x.cash_float_cents) },
            { t: "Pedidos (historial)", k: "pedidos", der: true }, { t: "Estado", v: (x) => x.active ? <Tag c="g">Activo</Tag> : <Tag>Inactivo</Tag> },
            { t: "", min: true, v: (x) => <Acciones editar={() => form(x)} borrar={() => borrarCon(`¿Eliminar a ${x.name}?`, `/api/admin/repartidores/${x.id}`, carga.recargar)} /> }]} />
      </>;
    }}</Cargando>
  </>;
}

/* ---------------- Usuarios ---------------- */
export function Usuarios() {
  const carga = useCarga(() => get("/api/usuarios"), []);
  return <>
    <Encabezado titulo="Usuarios" sub="Cada usuario ve solo las secciones que tiene permitidas." />
    <Cargando carga={carga}>{(r) => {
      const grupos = {}; Object.entries(r.permisos).forEach(([k, v]) => { (grupos[v.grupo] = grupos[v.grupo] || []).push([k, v.label]); });
      const form = (u) => formulario({
        titulo: u ? `Editar ${u.name}` : "Nuevo usuario", tam: "gr",
        campos: [{ id: "name", label: "Nombre", valor: u ? u.name : "", requerido: true }, { id: "username", label: "Usuario (para ingresar)", valor: u ? u.username : "", requerido: true },
          { id: "role", label: "Rol", tipo: "select", valor: u ? u.role : "cajero", opciones: Object.entries(r.roles) },
          { id: "password", label: u ? "Contraseña nueva (vacío = no cambia)" : "Contraseña", tipo: "clave", requerido: !u },
          { id: "active", label: "Activo (puede ingresar)", tipo: "check", valor: u ? !!u.active : true, full: true },
          { id: "permisos", tipo: "custom", full: true, valor: u ? u.permisos : r.presets.cajero, render: (v, set) => { const admin = v.role === "admin"; return <>
            <div className="etq" style={{ marginBottom: 6 }}>Permisos {admin && <span className="t3">(el administrador puede todo)</span>}
              {!admin && <Btn v="fant" ch onClick={() => set("permisos", r.presets[v.role] || [])}>Usar los del rol</Btn>}</div>
            {Object.entries(grupos).map(([g, ps]) => <div key={g} className="mt-s"><div className="t3 tiny b7" style={{ letterSpacing: ".7px", textTransform: "uppercase", marginBottom: 6 }}>{g}</div>
              <div className="permisos">{ps.map(([k, t]) => <label key={k} className="chk"><input type="checkbox" disabled={admin} checked={admin || v.permisos.includes(k)} onChange={(e) => set("permisos", e.target.checked ? [...v.permisos, k] : v.permisos.filter((x) => x !== k))} />{t}</label>)}</div></div>)}</>; } }],
        guardar: async (d) => { if (u) await put(`/api/usuarios/${u.id}`, d); else await post("/api/usuarios", d); toast("Usuario guardado", d.name, "ok"); carga.recargar(); },
      });
      return <>
        <div className="filtros"><span className="crece" /><Btn v="prim" icono={Plus} onClick={() => form(null)}>Nuevo usuario</Btn></div>
        <Tabla filas={r.usuarios} apagada={(u) => !u.active}
          columnas={[{ t: "Usuario", v: (u) => <div className="fila"><div className="avatar">{iniciales(u.name)}</div><div><b>{u.name}</b><div className="t3 small">{u.username}</div></div></div> },
            { t: "Rol", v: (u) => <div className="fila wrap" style={{ gap: 6 }}><Tag c={u.role === "admin" ? "v" : ""}>{u.role_label}</Tag>{u.acceso_libre && <Tag c="p">Acceso libre</Tag>}</div> },
            { t: "Puede", v: (u) => u.role === "admin" ? <span className="small t2">Todo</span> : <span className="small t2">{u.permisos.map((p) => (r.permisos[p] || {}).label || p).join(" · ") || "—"}</span> },
            { t: "Último ingreso", v: (u) => u.last_login ? fechaHora(u.last_login) : <span className="t3">—</span> },
            { t: "", min: true, v: (u) => <Acciones editar={() => form(u)} borrar={() => borrarCon(`¿Eliminar el usuario «${u.username}»?`, `/api/usuarios/${u.id}`, carga.recargar)} /> }]} />
      </>;
    }}</Cargando>
  </>;
}
