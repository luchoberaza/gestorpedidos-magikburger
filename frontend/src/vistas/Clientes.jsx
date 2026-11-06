import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Clock, Crown, FileSpreadsheet, MapPin, Phone, ShoppingBag, Star, Trophy, Users, Wallet } from "lucide-react";
import { descargar, fecha, get, numero, plata, qs, toast } from "../lib";
import { Btn, Cargando, Kpi, Seg, Vacio, Vidrio, useCarga } from "../ui";
import { Encabezado } from "./Operacion";

/* ---------------- Mejores clientes ----------------
   Quién más pide en el mes (o en los últimos meses). Al cliente lo reconoce la
   dirección del pedido; el cálculo vive en magik/dominio/clientes.py. */

// Lo último que se miró, mientras la app está abierta.
let recuerdo = { periodo: "", orden: "pedidos" };
const pct = (x) => `${Math.round((x || 0) * 100)}%`;

export default function Clientes() {
  const [periodo, setPeriodo] = useState(recuerdo.periodo);
  const [orden, setOrden] = useState(recuerdo.orden);
  useEffect(() => { recuerdo = { periodo, orden }; }, [periodo, orden]);
  const carga = useCarga(() => get("/api/clientes/mejores?" + qs({ periodo, orden })), [periodo, orden]);
  const exportar = () => {
    descargar("/api/clientes/mejores.xlsx?" + qs({ periodo, orden }));
    toast("Generando Excel", "Todos los clientes del período", "ok");
  };

  return <>
    <Encabezado titulo="Mejores clientes" sub="Los que más compran, reconocidos por la dirección del pedido">
      <Btn icono={FileSpreadsheet} onClick={exportar}>Exportar a Excel</Btn>
    </Encabezado>
    <Cargando carga={carga}>{(d) => {
      const top = d.clientes;
      const porPlata = d.orden === "plata";
      const parteP = top.reduce((s, c) => s + c.parte_pedidos, 0);
      const parteV = top.reduce((s, c) => s + c.parte_ventas, 0);
      const max = Math.max(1, ...top.map((c) => (porPlata ? c.total_cents : c.pedidos)));
      return <>
        <div className="fila wrap sb mb">
          <Seg opciones={d.periodos.map((p) => [p.id, p.corto])} valor={d.periodo.id} onChange={setPeriodo} />
          <Seg opciones={[["pedidos", "Más pedidos", ShoppingBag], ["plata", "Más gastaron", Wallet]]} valor={d.orden} onChange={setOrden} />
        </div>
        <h3>{d.periodo.etiqueta}</h3>
        <div className="grilla g3">
          <Kpi c="v" icono={Users} label="Clientes que pidieron" valor={numero(d.clientes_distintos)} sub="Direcciones distintas" />
          <Kpi c="p" icono={ShoppingBag} label="Pedidos" valor={numero(d.pedidos_total)} sub={`${plata(d.ventas_total_cents)} vendidos, sin envíos`} />
          <Kpi destacado c="a" icono={Trophy} label={`Los ${top.length || 5} mejores`}
            valor={top.length ? pct(porPlata ? parteV : parteP) : "—"}
            sub={!top.length ? "Sin pedidos en el período" : porPlata ? `de lo vendido · ${pct(parteP)} de los pedidos` : `de los pedidos · ${pct(parteV)} de lo vendido`} />
        </div>
        <Vidrio className="mt" titulo={`Top ${top.length || 5}`} extra={<span className="t3 small">{porPlata ? "Por lo que gastaron" : "Por cantidad de pedidos"}</span>}>
          {top.length
            ? <div className="mc-lista">{top.map((c, i) => <FilaCliente key={c.direccion} c={c} i={i} porPlata={porPlata} max={max} />)}</div>
            : <Vacio icono={Trophy} titulo="Todavía no hay pedidos en este período" sub="Apenas se tomen pedidos, acá aparecen los clientes que más compran." />}
        </Vidrio>
        <p className="t3 small mt">Cuenta los pedidos guardados, sin el envío. La misma dirección escrita con o sin mayúsculas, tildes o puntos es el mismo cliente; otro apartamento es otro cliente. Los pedidos cerrados antes de esta versión no quedaron guardados (el cierre anterior los borraba), así que esos meses aparecen vacíos.</p>
      </>;
    }}</Cargando>
  </>;
}

function FilaCliente({ c, i, porPlata, max }) {
  const valor = porPlata ? c.total_cents : c.pedidos;
  return (
    <motion.div layout className={`mc-fila ${c.puesto === 1 ? "primero" : ""}`}
      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .25, delay: i * .04 }}>
      <div className={`mc-puesto p${c.puesto}`} title={`Puesto ${c.puesto}`}>{c.puesto === 1 ? <Crown /> : c.puesto}</div>
      <div className="crece">
        <div className="mc-dir"><MapPin /><span className="trunc">{c.direccion}</span></div>
        <div className="mc-meta">
          {c.telefonos.length > 0 && <span><Phone />{c.telefonos.join(" · ")}</span>}
          <span><Clock />Último pedido: {fecha(c.ultimo)}</span>
          {c.favorito && <span><Star /><span>Lo que más pide: <b>{c.favorito.nombre}</b> ×{numero(c.favorito.unidades)}</span></span>}
        </div>
        <div className="mc-barra"><motion.i initial={{ width: 0 }} animate={{ width: `${Math.max(3, (valor / max) * 100)}%` }} transition={{ duration: .6, delay: i * .06 }} /></div>
      </div>
      <div className="mc-num">
        <div><b>{numero(c.pedidos)}</b><small>{c.pedidos === 1 ? "pedido" : "pedidos"}</small></div>
        <div><b>{plata(c.total_cents)}</b><small>promedio {plata(c.ticket_promedio_cents)}</small></div>
      </div>
    </motion.div>
  );
}
