# Decisiones — Proyecto Magik

Certeza: `Confirmado` (con fuente) · `Inferido` · `Supuesto` (configurable) · `Duda abierta`.

| # | Fecha | Decisión | Certeza | Por qué / dónde se cambia |
|---|---|---|---|---|
| D-01 | 2026-11-06 | Un solo software: el panel financiero de *Caja Hamburguesería* entra en Magik (no al revés). Se reimplementan sus funciones; su código no se copia. | Confirmado (pedido del responsable) | Magik es el que ya vive en la PC del local y se actualiza por OTA. |
| D-02 | 2026-11-06 | El cierre de jornada **ya no borra** los pedidos: los pasa al historial (`orders.cierre_id`). El tablero sigue quedando vacío después del cierre, igual que antes. | Inferido | Sin historial no hay reportes ni ventas por período. |
| D-03 | 2026-11-06 | Jornada con hora de corte (por defecto 06:00): un pedido de la madrugada cuenta para la noche anterior. | Supuesto | Configuración → Operación. 0 = día calendario. |
| D-04 | 2026-11-06 | El envío es del repartidor: no cuenta como venta del local en Inicio/Ventas/Reportes (se muestra aparte). | Confirmado (regla de liquidación vigente) | Misma regla que ya usaba la liquidación. |
| D-05 | 2026-11-06 | El stock se descuenta **al cerrar la jornada**, según recetas; mientras tanto se muestra como "comprometido". | Inferido | Editar o borrar pedidos durante el día nunca deja el stock desfasado. |
| D-06 | 2026-11-06 | La caja entra sin contraseña ("acceso libre" del usuario *Caja*), como siempre; administrar requiere usuario con permiso. | Inferido | Configuración → Acceso (se puede exigir usuario siempre). |
| D-07 | 2026-11-06 | El usuario *admin* sigue con la contraseña que ya usaba el local. La app **no** pide cambiarla; quien quiera puede hacerlo desde su menú. | Confirmado (responsable: "Nadie dijo para cambiar la contraseña del admin") | Usuario → Cambiar mi contraseña (opcional). |
| D-08 | 2026-11-06 | Medios de pago configurables; cada uno dice si es efectivo (se rinde) y si el ticket sale sin precios. Se conservan los códigos `cash` y `transfer` de los pedidos existentes. | Confirmado (comportamiento anterior) | Configuración → Medios de pago. |
| D-09 | 2026-11-06 | Interfaz en React compilada a `static/dist/` (un script clásico, sin CDN): funciona sin internet y viaja por OTA como texto. Identidad visual del gestor original (vidrio, degradé violeta→rosa, píldoras arriba). | Confirmado (pedido del responsable: "un rediseño del gestor original") | `frontend/` → `npm run build`. |
| D-10 | 2026-11-06 | Excel generado a mano (zip + XML) y PDF con reportlab (ya se usaba): no se agregan dependencias a la PC del cliente. | Inferido | `magik/documentos.py`. |
| D-11 | 2026-11-06 | Mejores clientes: al cliente lo reconoce la **dirección** (el pedido no guarda nombre); se agrupa sin importar mayúsculas, tildes, puntos ni espacios, y otro apartamento es otro cliente. | Confirmado (responsable: "lo que los verifica son las direcciones") | `magik/dominio/clientes.py`. |
| D-12 | 2026-11-06 | El ranking ordena por cantidad de pedidos (con un botón para ordenar por lo gastado); lo gastado es sin envío; se elige el mes actual, cada uno de los 5 anteriores o los 6 juntos; se muestran 5 y el Excel trae todos. | Supuesto (pidió "el que más pide" y "el que más compró") | Negocio → Mejores clientes. |
| D-13 | 2026-11-06 | Los meses anteriores a la 2.1 aparecen vacíos: el cierre viejo borraba los pedidos (`DELETE FROM orders` en `admin_clear_orders`), así que no hay de dónde sacarlos. La pantalla lo aclara. | Confirmado (código anterior, `b66af89:app.py`) | — |
