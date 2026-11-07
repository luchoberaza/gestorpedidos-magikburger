# Libro de pedidos — Proyecto Magik

Estados: `nuevo` → `en curso` → `hecho-local` → `verificado` · `parcial (falta: …)` · `bloqueado` · `descartado`.
"¿Quedó algo?" se responde filtrando este libro por lo que no está `verificado` ni `descartado`.

| ID | Fecha | Fuente | Pedido (cita) | Dónde | Clase | Rama | Estado | Evidencia |
|---|---|---|---|---|---|---|---|---|
| P-001 | 2026-11-06 | Responsable (sesión) | "le quiero mezclar este software […] que sea solo un software" — fusionar las funciones de *Caja Hamburguesería* (panel financiero) dentro de Magik | toda la app | 🟢 | main | esperando deploy | tests 29/29 · `main@7d0d8d1` · release OTA `b1793961123` · humo local en copia de la instalación |
| P-002 | 2026-11-06 | Responsable (sesión) | "esta muy hardcodeada […] que no haya hardcode" | backend + UI | 🟢 | main | esperando deploy | tests 29/29 · `main@7d0d8d1` · release OTA `b1793961123` · humo local en copia de la instalación |
| P-003 | 2026-11-06 | Responsable (sesión) | "mejora la interfaz visual" | UI completa | 🟢 | main | esperando deploy | tests 29/29 · `main@7d0d8d1` · release OTA `b1793961123` · humo local en copia de la instalación |
| P-004 | 2026-11-06 | Responsable (sesión) | "levantada mal […] que funcione perfecto" — arquitectura, historial, migraciones, pruebas | backend | 🟢 | main | esperando deploy | tests 29/29 · `main@7d0d8d1` · release OTA `b1793961123` · humo local en copia de la instalación |
| P-005 | 2026-11-06 | repo | Trabajo de liquidación del 9-ago que había quedado sin commitear (kind del envío, pendientes, arrastrados, verificación de total) | liquidación | 🟢 | main | esperando deploy | `tests/test_magik.py` (liquidación y 800 jornadas al azar) · `main@7d0d8d1` |
| P-006 | 2026-11-06 | Responsable (sesión) | "un re diseño del gestor original […] agarres ese estilo de diseño y lo remodeles dejandolo mas lindo usando elementos de react" | UI completa | 🟢 | main | esperando deploy | React + framer-motion en `frontend/` → `static/dist` · recorrido Playwright de todas las pantallas · `main@7d0d8d1` |
| P-007 | 2026-11-06 | Responsable (sesión) | "Nadie dijo para cambiar la contraseña del admin" — sacar el aviso que lo pedía | sesión / barra superior | 🟢 | main | esperando deploy | `test_admin_entra_con_la_clave_de_siempre_y_la_caja_sin_clave` · Playwright: el admin entra sin aviso y "Cambiar mi contraseña" sigue andando |
| P-008 | 2026-11-06 | Responsable (sesión) | "una analitica del cliente que mas pide en el mes […] lo que los verifica son las direcciones […] 6 meses para atras […] los 5 que mas compraron" | Negocio → Mejores clientes | 🟢 | main | esperando deploy | `TestMejoresClientes` (7) · Playwright con 148 pedidos en 6 meses: meses, "6 meses", orden, Excel, celular, la Caja no la ve (403) |
| P-009 | 2026-11-06 | Responsable (sesión) | "construi el instalador completo y que este en github, que con ejecutarlo se instale todo y se abra sin vuelta" | `launcher/` + release `instalador` | 🟢 | main | en curso | — |

## Detalle de P-001 (qué se trae de Caja Hamburguesería)

| Función de Caja | Dónde queda en Magik |
|---|---|
| Dashboard (ventas/gastos/ganancia de hoy y del mes, gráficos) | **Inicio** |
| Registro de ventas manuales | **Ventas** (junto con los pedidos, que ahora quedan en historial) |
| Registro de gastos con categorías y medio de pago | **Gastos** |
| Recordatorios de pago de servicios | **Gastos → Pagos fijos** |
| Inventario, stock mínimo, movimientos, alerta de stock bajo | **Inventario** (y además descuenta por receta al cerrar la jornada) |
| Reportes (ganancias por período, comparación de meses, top productos) | **Reportes** |
| Exportar a Excel y PDF | **Reportes** y botones de exportar en cada tabla |
| Usuarios con roles | **Usuarios** (con permisos por usuario) |
| Backup / restaurar | **Configuración → Respaldo** |
| Configuración (nombre del negocio, umbral de gasto) | **Configuración** |
| Búsqueda global, tema claro/oscuro, notificaciones | Barra superior |
| Sus datos | **Configuración → Importar desde Caja Hamburguesería** |

> «Esperando deploy»: la versión ya está publicada; se aplica sola en la PC del local la próxima vez que abran la app. Pasa a `verificado` con el humo en el local.
