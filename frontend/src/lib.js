/* Utilidades sin interfaz: API, formato, fechas y un pequeño bus de eventos. */

/* ---------------- Bus de eventos ---------------- */
const oyentes = {};
export const bus = {
  on(ev, fn) { (oyentes[ev] = oyentes[ev] || new Set()).add(fn); return () => oyentes[ev].delete(fn); },
  emit(ev, dato) { (oyentes[ev] || []).forEach((fn) => fn(dato)); },
};
export const toast = (titulo, detalle, tipo) => bus.emit("toast", { titulo, detalle, tipo: tipo || "info", id: Math.random() });

/* ---------------- Sesión (fuera de React, para formato) ---------------- */
export const global = { sesion: null };

/* ---------------- API ---------------- */
export class ErrorApi extends Error {
  constructor(msg, datos, status) { super(msg); this.datos = datos || {}; this.status = status; }
}
export async function api(url, opts = {}) {
  const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  let r;
  try { r = await fetch(url, init); }
  catch (e) { throw new ErrorApi("No hay conexión con el servidor de la app. Si recién la abriste, esperá unos segundos y volvé a intentar.", {}, 0); }
  let j = null;
  try { j = await r.json(); } catch (e) { j = null; }
  if (r.status === 401 && j && j.login) { bus.emit("sin-sesion", j.error); throw new ErrorApi(j.error, j, 401); }
  if (!r.ok || !j || j.ok === false) throw new ErrorApi((j && j.error) || `El servidor respondió con un error (${r.status}).`, j || {}, r.status);
  return j;
}
export const get = (u) => api(u);
export const post = (u, b) => api(u, { method: "POST", body: b || {} });
export const put = (u, b) => api(u, { method: "PUT", body: b || {} });
export const del = (u) => api(u, { method: "DELETE" });
export const qs = (o) => Object.entries(o).filter(([, v]) => v !== "" && v != null).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
export function descargar(url) {
  const a = document.createElement("a");
  a.href = url; a.download = ""; a.style.display = "none";
  document.body.appendChild(a); a.click(); setTimeout(() => a.remove(), 500);
}
/** Ejecuta y, si falla, avisa con un toast. Devuelve el resultado o null. */
export async function intentar(fn, titulo) {
  try { return await fn(); }
  catch (e) { if (e.status !== 401) toast(titulo || "No se pudo completar", e.message, "error"); return null; }
}

/* ---------------- Formato ---------------- */
const pad = (n) => String(n).padStart(2, "0");
const miles = (n) => Math.trunc(Math.abs(n)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
export const simbolo = () => (global.sesion && global.sesion.negocio && global.sesion.negocio.simbolo) || "$";
export function plata(cents, sinSimbolo) {
  const c = Math.round(Number(cents || 0));
  let t = miles(c / 100);
  if (c % 100) t += "," + pad(Math.abs(c % 100));
  if (c < 0) t = "−" + t;
  return sinSimbolo ? t : `${simbolo()} ${t}`;
}
export const pesos = (cents) => { const c = Math.round(Number(cents || 0)); return c % 100 ? (c / 100).toFixed(2).replace(".", ",") : String(c / 100); };
export function numero(x) { const n = Number(x || 0); return Number.isInteger(n) ? (n < 0 ? "−" : "") + miles(n) : n.toLocaleString("es-UY", { maximumFractionDigits: 3 }); }
export const fecha = (iso) => { const s = String(iso || ""); return s.length >= 10 ? `${s.slice(8, 10)}/${s.slice(5, 7)}/${s.slice(0, 4)}` : s; };
export const fechaCorta = (iso) => { const s = String(iso || ""); return s.length >= 10 ? `${s.slice(8, 10)}/${s.slice(5, 7)}` : s; };
export const hora = (iso) => { const s = String(iso || "").replace("T", " "); return s.length >= 16 ? s.slice(11, 16) : ""; };
export const fechaHora = (iso) => `${fechaCorta(iso)} ${hora(iso)}`;
export function hace(iso) {
  if (!iso) return "";
  const min = Math.max(0, Math.round((Date.now() - new Date(String(iso).replace(" ", "T")).getTime()) / 60000));
  if (min < 1) return "recién";
  if (min < 60) return `hace ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `hace ${h} h${min % 60 ? ` ${min % 60} min` : ""}`;
  return fechaHora(iso);
}
export const iniciales = (n) => String(n || "?").trim().split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase();
export function aCentavos(txt) {
  let s = String(txt == null ? "" : txt).replace(/\$/g, "").replace(/\s/g, "");
  if (!s) return null;
  const neg = /^[-−]/.test(s);
  s = s.replace(/^[+\-−]/, "");
  if (s.includes(",") && s.includes(".")) s = s.replace(/\./g, "").replace(",", ".");
  else if (s.includes(",")) s = s.replace(",", ".");
  else if ((s.match(/\./g) || []).length > 1 || /^\d{1,3}\.\d{3}$/.test(s)) s = s.replace(/\./g, "");
  const n = parseFloat(s);
  return isNaN(n) ? null : Math.round(n * 100) * (neg ? -1 : 1);
}

/* ---------------- Fechas y períodos ---------------- */
const parse = (s) => { const [y, m, d] = String(s).split("-").map(Number); return new Date(y, m - 1, d); };
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const hoy = () => (global.sesion && global.sesion.hoy) || iso(new Date());
export const sumarDias = (s, n) => { const d = parse(s); d.setDate(d.getDate() + n); return iso(d); };
export function rango(clave, custom) {
  const h = hoy(), d = parse(h);
  switch (clave) {
    case "hoy": return { desde: h, hasta: h };
    case "ayer": { const a = sumarDias(h, -1); return { desde: a, hasta: a }; }
    case "semana": { const lun = sumarDias(h, -((d.getDay() + 6) % 7)); return { desde: lun, hasta: sumarDias(lun, 6) }; }
    case "mes": return { desde: iso(new Date(d.getFullYear(), d.getMonth(), 1)), hasta: iso(new Date(d.getFullYear(), d.getMonth() + 1, 0)) };
    case "mesant": return { desde: iso(new Date(d.getFullYear(), d.getMonth() - 1, 1)), hasta: iso(new Date(d.getFullYear(), d.getMonth(), 0)) };
    case "anio": return { desde: `${d.getFullYear()}-01-01`, hasta: `${d.getFullYear()}-12-31` };
    case "rango": return { desde: (custom && custom.desde) || "", hasta: (custom && custom.hasta) || "" };
    default: return { desde: "", hasta: "" };
  }
}
export const periodo = (per) => ({ per, ...rango(per) });
export const textoPeriodo = (p) => (!p.desde && !p.hasta) ? "todo el historial" : p.desde === p.hasta ? fecha(p.desde) : `${fecha(p.desde)} al ${fecha(p.hasta)}`;

/* ---------------- Permisos ---------------- */
export const puede = (p) => !!(global.sesion && global.sesion.usuario && global.sesion.usuario.permisos.includes(p));

/* ---------------- Impresión del ticket ---------------- */
export async function imprimirTicket(orderId, copias) {
  copias = Math.max(1, Math.min(10, parseInt(copias || 1, 10)));
  try {
    const r = await fetch(`/orders/${orderId}/print_escpos?copies=${copias}`, { method: "POST" }).then((x) => x.json());
    if (r && r.ok) { toast("Ticket impreso", `En ${r.printer}`, "ok"); return; }
  } catch (e) { /* sigue con la impresión de Windows */ }
  let f = document.getElementById("marco-ticket");
  if (f) f.remove();
  f = document.createElement("iframe");
  f.id = "marco-ticket";
  f.setAttribute("aria-hidden", "true");
  f.style.cssText = "position:fixed;width:0;height:0;border:0;right:0;bottom:0;opacity:0";
  f.src = `/orders/${orderId}/ticket?copies=${copias}`;
  f.onload = () => { try { f.contentWindow.focus(); f.contentWindow.print(); } catch (e) { window.location.href = `/orders/${orderId}/ticket?copies=${copias}&autoprint=1`; } };
  document.body.appendChild(f);
}
