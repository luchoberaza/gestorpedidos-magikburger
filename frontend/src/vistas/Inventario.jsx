import { useState } from "react";
import { AlertTriangle, ArrowLeftRight, Boxes, Coins, Pencil, Plus, Trash2, X, Info } from "lucide-react";
import { bus, del, fecha, get, hora, intentar, numero, pesos, plata, post, put, toast } from "../lib";
import { Aviso, Btn, Cargando, Kpi, Modal, Tabla, Tabs, Tag, abrir, confirmar, formulario, useCarga } from "../ui";
import { Encabezado } from "./Operacion";
import { opciones } from "./Finanzas";

const TIPOS = { entrada: ["Entrada", "g"], salida: ["Salida", "r"], ajuste: ["Conteo", "a"], consumo: ["Consumo", "v"], venta: ["Venta", "v"] };

export default function Inventario({ params }) {
  const [tab, setTab] = useState((params && params.tab) || "insumos");
  return <>
    <Encabezado titulo="Inventario" sub="Insumos, stock mínimo, movimientos y recetas" />
    <Tabs valor={tab} onChange={setTab} tabs={[["insumos", "Insumos y stock"], ["movimientos", "Movimientos"], ["recetas", "Recetas (descuento automático)"]]} />
    {tab === "insumos" && <Insumos />}
    {tab === "movimientos" && <Movimientos />}
    {tab === "recetas" && <Recetas />}
  </>;
}

function Insumos() {
  const [soloBajos, setSoloBajos] = useState(false);
  const carga = useCarga(() => Promise.all([get("/api/insumos"), opciones()]), []);
  const recargar = () => { carga.recargar(); bus.emit("avisos"); };
  return <Cargando carga={carga}>{([r, op]) => {
    const lista = r.insumos.filter((i) => !soloBajos || (i.bajo && i.activo));
    return <>
      <div className="grilla g3 mb">
        <Kpi c="v" icono={Boxes} label="Insumos activos" valor={String(r.insumos.filter((i) => i.activo).length)} />
        <Kpi c={r.bajos ? "r" : "g"} icono={AlertTriangle} label="Con stock bajo" valor={String(r.bajos)} sub="Disponible en o bajo el mínimo" />
        <Kpi destacado c="p" icono={Coins} label="Valor del inventario" valor={plata(r.valor_total_cents)} sub="Stock × costo unitario" />
      </div>
      <div className="filtros"><label className="chk"><input type="checkbox" checked={soloBajos} onChange={(e) => setSoloBajos(e.target.checked)} />Ver solo stock bajo</label><span className="crece" /><Btn v="prim" icono={Plus} onClick={() => formInsumo(null, op, recargar)}>Nuevo insumo</Btn></div>
      <Tabla filas={lista} apagada={(i) => !i.activo} vacio={soloBajos ? "No hay insumos con stock bajo" : "Todavía no cargaste insumos"} vacioSub={soloBajos ? "" : "Cargá pan, carne, queso, papas, bebidas… y su stock mínimo para recibir avisos."}
        columnas={[{ t: "Insumo", v: (i) => <><b>{i.nombre}</b> {i.categoria && <Tag>{i.categoria}</Tag>} {i.origen === "caja" && <span className="t3 small">importado</span>}</> },
          { t: "Stock", der: true, v: (i) => <><b>{numero(i.stock)}</b> <span className="t3 small">{i.unidad}</span></> },
          { t: "Comprometido", der: true, v: (i) => i.comprometido ? <span className="av-t">−{numero(i.comprometido)}</span> : <span className="t3">—</span> },
          { t: "Disponible", der: true, v: (i) => <b className={i.bajo ? "mal-t" : ""}>{numero(i.disponible)}</b> },
          { t: "Mínimo", der: true, v: (i) => numero(i.stock_minimo) }, { t: "Costo unit.", der: true, v: (i) => i.costo_unit_cents ? plata(i.costo_unit_cents) : "—" },
          { t: "Estado", v: (i) => !i.activo ? <Tag>Inactivo</Tag> : i.bajo ? <Tag c="r">Stock bajo</Tag> : <Tag c="g">OK</Tag> },
          { t: "", min: true, v: (i) => <div className="fila" style={{ gap: 4, justifyContent: "flex-end" }}><Btn ch icono={ArrowLeftRight} onClick={() => formMovimiento(i, op, recargar)}>Movimiento</Btn>
            <div className="acc"><Btn icono={Pencil} title="Editar" onClick={() => formInsumo(i, op, recargar)} /><Btn className="del" icono={Trash2} title="Eliminar" onClick={async () => {
              if (!(await confirmar(`¿Eliminar «${i.nombre}»? Si tiene movimientos o recetas, queda desactivado.`, { ok: "Eliminar", peligro: true }))) return;
              const x = await intentar(() => del(`/api/insumos/${i.id}`)); if (x) { toast(x.mensaje, "", "ok"); recargar(); } }} /></div></div> }]} />
      <p className="t3 small mt">«Comprometido» es lo que van a consumir los pedidos abiertos según las recetas. Se descuenta del stock al cerrar la jornada.</p>
    </>;
  }}</Cargando>;
}

function formInsumo(i, op, alGuardar) {
  return formulario({
    titulo: i ? `Editar ${i.nombre}` : "Nuevo insumo",
    campos: [{ id: "nombre", label: "Nombre", valor: i ? i.nombre : "", full: true, requerido: true, placeholder: "Ej: Pan de hamburguesa" },
      { id: "categoria", label: "Categoría", valor: i ? i.categoria : "", lista: op.categorias_gasto, placeholder: "Opcional" },
      { id: "unidad", label: "Unidad", tipo: "select", valor: i ? i.unidad : (op.unidades[0] || "unidades"), opciones: [...new Set([...(i ? [i.unidad] : []), ...op.unidades])] },
      ...(i ? [] : [{ id: "stock", label: "Stock inicial", tipo: "numero", valor: 0, min: 0 }]),
      { id: "stock_minimo", label: "Stock mínimo (aviso)", tipo: "numero", valor: i ? i.stock_minimo : 0, min: 0 },
      { id: "costo_unit", label: "Costo por unidad", tipo: "dinero", valor: i && i.costo_unit_cents ? pesos(i.costo_unit_cents) : "" },
      { id: "activo", label: "Activo", tipo: "check", valor: i ? !!i.activo : true }],
    guardar: async (d) => { if (i) await put(`/api/insumos/${i.id}`, d); else await post("/api/insumos", d); toast(i ? "Insumo actualizado" : "Insumo creado", d.nombre, "ok"); alGuardar(); },
  });
}

function formMovimiento(i, op, alGuardar) {
  return formulario({
    titulo: `Movimiento de stock · ${i.nombre}`, ok: "Registrar", foco: "cantidad",
    intro: <div className="vidrio pad mb fila sb"><span>Stock actual</span><b className="num">{numero(i.stock)} {i.unidad}</b></div>,
    campos: [{ id: "tipo", label: "Tipo", tipo: "select", full: true, valor: "entrada", opciones: [["entrada", "Entrada (compra / reposición)"], ["salida", "Salida (merma / uso interno)"], ["conteo", "Conteo físico (fija el stock real)"]] },
      { id: "cantidad", label: "Cantidad", tipo: "numero", min: 0, requerido: true, ayuda: "En el conteo, poné lo que contaste." },
      { id: "motivo", label: "Motivo", placeholder: "Opcional" },
      { id: "registrar_gasto", label: "Registrar la compra como gasto", tipo: "check", valor: false, full: true, mostrar: (v) => v.tipo === "entrada" },
      { id: "costo_total", label: "Costo total de la compra", tipo: "dinero", mostrar: (v) => v.tipo === "entrada" && v.registrar_gasto },
      { id: "proveedor", label: "Proveedor", mostrar: (v) => v.tipo === "entrada" && v.registrar_gasto },
      { id: "metodo_pago", label: "Medio de pago", tipo: "select", valor: op.medios[0], opciones: op.medios, mostrar: (v) => v.tipo === "entrada" && v.registrar_gasto }],
    guardar: async (d) => {
      const r = await post(`/api/insumos/${i.id}/movimiento`, { ...d, registrar_gasto: d.tipo === "entrada" && d.registrar_gasto ? 1 : 0 });
      toast("Stock actualizado", r.mensaje || `${i.nombre}: ${numero(r.stock)} ${i.unidad}${r.gasto_id ? " · gasto registrado" : ""}`, "ok"); alGuardar();
    },
  });
}

function Movimientos() {
  const carga = useCarga(() => get("/api/movimientos"), []);
  return <Cargando carga={carga}>{(r) => <Tabla filas={r.movimientos} vacio="Todavía no hay movimientos de stock"
    columnas={[{ t: "Fecha", v: (x) => <>{fecha(x.fecha)} <span className="t3 small">{hora(x.fecha)}</span></> }, { t: "Insumo", v: (x) => x.insumo || "(eliminado)" },
      { t: "Tipo", v: (x) => { const t = TIPOS[x.tipo] || [x.tipo, ""]; return <Tag c={t[1]}>{t[0]}</Tag>; } },
      { t: "Cantidad", der: true, v: (x) => <><b className={x.cantidad < 0 ? "mal-t" : "ok-t"}>{x.cantidad > 0 ? "+" : ""}{numero(x.cantidad)}</b> <span className="t3 small">{x.unidad}</span></> },
      { t: "Quedó en", der: true, v: (x) => x.stock_resultante == null ? "—" : numero(x.stock_resultante) },
      { t: "Motivo", v: (x) => x.motivo || "—" }, { t: "Quién", v: (x) => x.user_name || "—" }]} />}</Cargando>;
}

function Recetas() {
  const carga = useCarga(() => Promise.all([get("/api/recetas"), get("/api/insumos")]), []);
  return <Cargando carga={carga}>{([r, ins]) => {
    const activos = ins.insumos.filter((i) => i.activo);
    if (!activos.length) return <Aviso c="v" icono={Info}>Primero cargá los insumos en <b>Insumos y stock</b>. Después, acá indicás cuánto consume cada producto y el stock se descuenta solo al cerrar la jornada.</Aviso>;
    const opIns = activos.map((i) => [i.id, `${i.nombre} (${i.unidad})`]);
    const editarReceta = (p) => abrir(function EditarReceta({ cerrar, arriba }) {
      const [lineas, setLineas] = useState(p.receta.map((x) => ({ insumo_id: x.insumo_id, cantidad: x.cantidad })));
      const guardar = async () => { if (await intentar(() => put(`/api/recetas/producto/${p.id}`, { receta: lineas }))) { toast("Receta guardada", p.name, "ok"); cerrar(); carga.recargar(); } };
      return <Modal titulo={`Receta · ${p.name}`} onClose={() => cerrar()} arriba={arriba} pie={<><Btn v="fant" onClick={() => cerrar()}>Cancelar</Btn><Btn v="prim" onClick={guardar}>Guardar receta</Btn></>}>
        <p className="t2 small" style={{ marginTop: 0 }}>Cantidad de cada insumo por unidad vendida.</p>
        <div className="col s">
          {lineas.map((l, k) => <div key={k} className="fila">
            <select className="inp crece" value={l.insumo_id} onChange={(e) => setLineas(lineas.map((x, j) => j === k ? { ...x, insumo_id: +e.target.value } : x))}>{opIns.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select>
            <input className="inp" style={{ width: 110 }} type="number" step="any" min="0" value={l.cantidad} onChange={(e) => setLineas(lineas.map((x, j) => j === k ? { ...x, cantidad: e.target.value } : x))} />
            <Btn v="fant" ico icono={X} onClick={() => setLineas(lineas.filter((_, j) => j !== k))} /></div>)}
          {!lineas.length && <p className="t3 small">Sin insumos. Agregá el primero.</p>}
        </div>
        <Btn ch className="mt" icono={Plus} onClick={() => setLineas([...lineas, { insumo_id: activos[0].id, cantidad: 1 }])}>Agregar insumo</Btn>
      </Modal>;
    });
    const editarIng = (i) => formulario({
      titulo: `Insumo de «${i.name}»`, intro: <p className="t2 small" style={{ marginTop: 0 }}>Si se agrega como extra, se descuenta esta cantidad. Si se quita de la base, se deja de descontar.</p>,
      campos: [{ id: "insumo_id", label: "Insumo", tipo: "select", full: true, valor: i.insumo ? i.insumo.insumo_id : "", opciones: [["", "— No descuenta stock"], ...opIns] }, { id: "cantidad", label: "Cantidad por unidad", tipo: "numero", valor: i.insumo ? i.insumo.cantidad : 1, min: 0 }],
      guardar: async (d) => { await put(`/api/recetas/ingrediente/${i.id}`, d); carga.recargar(); },
    });
    return <>
      <div className="mb"><Aviso c="v" icono={Info}>Indicá cuánto lleva cada producto (ej: <b>Doble cheddar</b> = 1 pan, 2 medallones, 4 fetas). Al cerrar la jornada se descuenta lo vendido. Los extras ligados a un insumo suman al agregarse y restan al quitarse.</Aviso></div>
      <div className="grilla g2" style={{ alignItems: "start" }}>
        <div><h3>Productos</h3><Tabla filas={r.productos} apagada={(p) => !p.active} vacio="No hay productos" columnas={[{ t: "Producto", v: (p) => <b>{p.name}</b> },
          { t: "Receta", v: (p) => p.receta.length ? <div className="chips">{p.receta.map((x) => <Tag key={x.insumo_id} c="v">{numero(x.cantidad)} {x.unidad} {x.nombre}</Tag>)}</div> : <span className="t3 small">Sin receta</span> },
          { t: "", min: true, v: (p) => <Btn ch icono={Pencil} onClick={() => editarReceta(p)}>Editar</Btn> }]} /></div>
        <div><h3>Ingredientes y extras</h3><Tabla filas={r.ingredientes} apagada={(i) => !i.active} vacio="No hay ingredientes" columnas={[{ t: "Ingrediente", v: (i) => <b>{i.name}</b> },
          { t: "Insumo que usa", v: (i) => { if (!i.insumo) return <span className="t3 small">No descuenta</span>; const x = ins.insumos.find((a) => a.id === i.insumo.insumo_id); return x ? `${numero(i.insumo.cantidad)} ${x.unidad} de ${x.nombre}` : "—"; } },
          { t: "", min: true, v: (i) => <Btn ch icono={Pencil} onClick={() => editarIng(i)} /> }]} /></div>
      </div>
    </>;
  }}</Cargando>;
}
