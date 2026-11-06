# Libro de pedidos — Proyecto Magik

Estados: `nuevo` → `en curso` → `hecho-local` → `verificado` · `parcial (falta: …)` · `bloqueado` · `descartado`.
"¿Quedó algo?" se responde filtrando este libro por lo que no está `verificado` ni `descartado`.

| ID | Fecha | Fuente | Pedido (cita) | Dónde | Clase | Rama | Estado | Evidencia |
|---|---|---|---|---|---|---|---|---|
| P-001 | 2026-11-06 | Responsable (sesión) | "le quiero mezclar este software […] que sea solo un software" — fusionar las funciones de *Caja Hamburguesería* (panel financiero) dentro de Magik | toda la app | 🟢 | main | hecho-local | ver cierre de sesión |
| P-002 | 2026-11-06 | Responsable (sesión) | "esta muy hardcodeada […] que no haya hardcode" | backend + UI | 🟢 | main | hecho-local | ver cierre de sesión |
| P-003 | 2026-11-06 | Responsable (sesión) | "mejora la interfaz visual" | UI completa | 🟢 | main | hecho-local | ver cierre de sesión |
| P-004 | 2026-11-06 | Responsable (sesión) | "levantada mal […] que funcione perfecto" — arquitectura, historial, migraciones, pruebas | backend | 🟢 | main | hecho-local | ver cierre de sesión |
| P-005 | 2026-11-06 | repo | Trabajo de liquidación del 9-ago que había quedado sin commitear (kind del envío, pendientes, arrastrados, verificación de total) | liquidación | 🟢 | main | hecho-local | `tests/test_liquidacion.py` |

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
