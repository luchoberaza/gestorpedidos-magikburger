# Magik

Gestión de una hamburguesería en un solo programa: caja (tomar pedidos e imprimir
el ticket), seguimiento de pedidos, liquidación de repartidores y cierre de jornada,
más el panel del negocio (ventas, gastos, pagos fijos, inventario con recetas,
reportes, mejores clientes y exportación a Excel/PDF), usuarios con permisos, respaldos y la
importación de datos desde *Caja Hamburguesería*.

Funciona sin internet. Corre como servidor local (Flask + SQLite) y se abre desde
el launcher de escritorio (`launcher/`).

## Estructura

| Carpeta | Qué hay |
|---|---|
| `app.py` | Punto de entrada (el launcher lo carga y llama a `app.run`). |
| `magik/` | Backend: base y migraciones (`db.py`), configuración (`config.py`), usuarios y permisos (`auth.py`), reglas del negocio (`dominio/`), ticket e impresoras (`ticket.py`), PDF y Excel (`documentos.py`), importador (`importar_caja.py`) y la API (`rutas/`). |
| `frontend/` | Interfaz en React (Vite). Se compila a `static/dist/`. |
| `static/dist/` | Interfaz compilada (se versiona: el cliente no tiene Node). |
| `templates/` | `index.html` (carga la interfaz) y `ticket.html` (impresión común). |
| `tests/` | Pruebas (`unittest`), siempre contra bases temporales. |

## Correr en desarrollo

```bash
python app.py                      # http://127.0.0.1:5000  (MAGIK_DB_PATH elige la base)
python -m unittest discover -s tests -v
```

Interfaz (solo si se cambia algo en `frontend/`):

```bash
cd frontend
npm install
npm run build                      # deja static/dist/app.js y app.css
```

## Instalación

Instalador completo (trae Python y todo lo necesario):
[Instalar-Magik.exe](https://github.com/luchoberaza/gestorpedidos-magikburger/releases/download/instalador/Instalar-Magik.exe).
Paso a paso en [GUIA-INSTALACION-CLIENTE.md](GUIA-INSTALACION-CLIENTE.md); se arma con
`launcher\build_launcher.bat`.

## Actualizaciones

Ver [ACTUALIZACIONES.md](ACTUALIZACIONES.md): al pushear a `main`, el local recibe la
versión nueva la próxima vez que abre la app.
