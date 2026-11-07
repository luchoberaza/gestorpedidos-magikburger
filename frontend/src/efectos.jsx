/* Efectos de la interfaz: números que ruedan, brillo bajo el cursor, el borde del botón
   principal y el fondo del ingreso. Cada uno dice de qué componente gratuito sale. */
import { useEffect, useRef, useState } from "react";
import NumberFlow from "@number-flow/react";
import { simbolo } from "./lib";
import Grainient from "./rb/Grainient";

/* ---------- Números que ruedan: NumberFlow (number-flow.barvian.me, MIT) ---------- */
const FORMATO = (dec) => ({ minimumFractionDigits: dec, maximumFractionDigits: dec, useGrouping: "always" });

/** Plata en centavos, con el mismo formato que plata(): "$ 1.630" o "$ 1.630,50". */
export function Monto({ cents, className }) {
  const c = Math.round(Number(cents || 0));
  return <NumberFlow className={className} value={c / 100} locales="es-UY" format={FORMATO(c % 100 ? 2 : 0)} prefix={`${simbolo()} `} willChange />;
}

/** Recibe un texto ya formateado: si es plata o un número, lo anima; si no, lo deja como está. */
export function ValorAnimado({ valor }) {
  if (typeof valor !== "string") return valor;
  const sim = simbolo().replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = new RegExp(`^${sim} (−?)([\\d.]+)(?:,(\\d{2}))?$`).exec(valor);
  if (m) {
    const n = Number(m[2].replace(/\./g, "") + (m[3] ? "." + m[3] : "")) * (m[1] ? -1 : 1);
    return <NumberFlow value={n} locales="es-UY" format={FORMATO(m[3] ? 2 : 0)} prefix={`${simbolo()} `} />;
  }
  if (/^−?[\d.]+$/.test(valor) && valor.length < 12) {
    const n = Number(valor.replace(/\./g, "").replace("−", "-"));
    return <NumberFlow value={n} locales="es-UY" format={FORMATO(0)} />;
  }
  return valor;
}

/* ---------- Brillo bajo el cursor (SpotlightCard de React Bits / MagicCard de Magic UI) ----------
   Un solo oyente por grilla: escribe --mx/--my en cada .brillo. Barato aunque haya 40 productos. */
export function useBrillo() {
  const ref = useRef(null);
  useEffect(() => {
    const zona = ref.current;
    if (!zona) return undefined;
    let raf = 0, x = 0, y = 0;
    const pintar = () => {
      raf = 0;
      zona.querySelectorAll(".brillo").forEach((el) => {
        const r = el.getBoundingClientRect();
        el.style.setProperty("--mx", `${x - r.left}px`);
        el.style.setProperty("--my", `${y - r.top}px`);
      });
    };
    const mover = (e) => { x = e.clientX; y = e.clientY; if (!raf) raf = requestAnimationFrame(pintar); };
    zona.addEventListener("pointermove", mover, { passive: true });
    zona.addEventListener("scroll", mover, { passive: true, capture: true });
    return () => { cancelAnimationFrame(raf); zona.removeEventListener("pointermove", mover); zona.removeEventListener("scroll", mover, { capture: true }); };
  }, []);
  return ref;
}

/** Borde que viaja alrededor del botón (BorderBeam de Magic UI, en CSS). El padre lleva la clase "listo". */
export const Haz = () => <span className="haz" aria-hidden="true" />;

/** Tema claro/oscuro observado desde el atributo de <html> (lo cambia App). */
export function useTema() {
  const leer = () => document.documentElement.getAttribute("data-theme") === "claro" ? "claro" : "oscuro";
  const [t, setT] = useState(leer);
  useEffect(() => {
    const mo = new MutationObserver(() => setT(leer()));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);
  return t;
}

/* ---------- Fondo del ingreso: Grainient de React Bits (degradé con grano, en WebGL) ---------- */
export function FondoLogin() {
  const claro = useTema() === "claro";
  return (
    <div className="login-fondo" aria-hidden="true">
      <Grainient key={claro ? "claro" : "oscuro"} colors={claro ? ["#eaf0ff", "#9cc3ff", "#d6b4ff", "#ffd27a"] : ["#03050f", "#0c1f63", "#1f5fe0", "#4c1d95"]}
        speed={0.5} grainAmount={claro ? 0.05 : 0.09} saturation={1.05} lightMode={claro} clickSwirl={false} />
    </div>
  );
}
