# Actualizaciones automáticas (OTA) — Proyecto Magik

Este sistema permite que, al hacer **commit + push a `main`**, el cliente reciba la
actualización **solo al abrir la app**, sin reinstalar nada y **sin tocar su base de datos**.

## Cómo funciona (resumen)

1. Pusheás cambios de `app.py`, `templates/` o `static/` a `main` del repo público
   `gestorpedidos-magikburger` (el repo de la app).
2. Una **GitHub Action** (`.github/workflows/publish-update.yml`) empaqueta esos archivos
   en un único `backend.json` y publica una **release** en ese mismo repo con un tag
   `b<build>` (build = epoch, siempre creciente). Usa el token que ya trae GitHub
   Actions, así que **no hay que crear ningún token**.
3. El **launcher** del cliente, al abrir, consulta la última release pública. Si hay un
   build más nuevo, baja `backend.json`, arma una copia del backend con esos archivos
   actualizados y la usa. Si no hay internet o algo falla, sigue con la versión actual
   (nunca se rompe).
4. La **base de datos** vive aparte, en una carpeta persistente del usuario, así que
   **ninguna actualización la pisa jamás**.

> Cubre el ~95% de los cambios (backend Python + UI web). Si algún día cambia el propio
> launcher de Electron (`launcher/main.js`), eso sí necesita un reempaquetado e instalación
> puntual (es raro).

---

## Puesta en marcha (una sola vez)

### 1) Subir el código al repo de la app

El launcher se actualiza leyendo las *releases* del repo público
`gestorpedidos-magikburger`, y la Action publica ahí. Así que el código (al menos
`app.py`, `templates/`, `static/` y la carpeta `.github/`) tiene que vivir en ese repo,
en la rama `main`. Subílo una vez:

```bash
git remote add app https://github.com/luchoberaza/gestorpedidos-magikburger.git
git push app main
```

(o cambiá el `origin` a ese repo). No hace falta crear ningún token: la Action usa el que
ya provee GitHub Actions. Solo asegurate de que en el repo estén habilitadas las Actions
(**Settings → Actions → General → Allow all actions**).

### 2) Compilar e instalar el launcher nuevo (una sola vez)

El launcher con OTA hay que llevarlo al cliente una vez:

```bash
cd launcher
npm install
npm run dist
```

Esto genera el instalador en `launcher/dist/`. Instalalo en la PC del cliente
**reemplazando** la versión vieja.

### 3) ⚠️ Rescatar la base de datos del cliente (¡IMPORTANTE, una sola vez!)

Hoy la base del cliente vive **dentro** de la instalación vieja. Al instalar la versión
nueva se reemplazan archivos, así que hay que rescatarla **antes**:

1. **Antes de instalar lo nuevo**, copiá a un lugar seguro (ej: Escritorio) el archivo:
   `...\resources\backend_app\magikburger.db`
   (está dentro de la carpeta donde está instalada la app actual del cliente).
2. Instalá la versión nueva.
3. Abrí la app **una vez** y cerrala (esto crea la carpeta de datos persistente).
4. Reemplazá el archivo:
   `%APPDATA%\MagikBurger Launcher\data\magikburger.db`
   por la base real que copiaste en el paso 1.
5. Volvé a abrir la app: ya usa los datos reales del cliente.

> Si no estás seguro de la ruta de `%APPDATA%`, abrí `launcher.log` (está en esa misma
> carpeta `%APPDATA%\MagikBurger Launcher\`): en cada arranque registra la línea
> `userData: ...` con la ruta exacta.

De acá en adelante, **la base nunca más se toca** por las actualizaciones.

---

## Día a día

Solo:

```bash
git add -A
git commit -m "lo que cambiaste"
git push
```

La Action publica la actualización y el cliente la recibe **la próxima vez que abra la app**.
No hay que reinstalar ni tocar nada en su PC.

## Notas

- Las actualizaciones se aplican **al iniciar** la app (necesita internet en ese momento;
  si no hay, abre con la versión que ya tenía y actualiza la próxima vez).
- El esquema de la base se migra solo (`ensure_schema()` en `app.py`), así que agregar
  columnas/tablas nuevas no rompe la base existente del cliente.
- Cambios en `launcher/main.js` (el shell de Electron) **no** viajan por OTA: requieren
  reempaquetar e instalar de nuevo (poco frecuente).
- Carpetas que crea el launcher en `%APPDATA%\MagikBurger Launcher\`:
  `data/` (la base), `backends/` (versiones del backend descargadas) y `launcher.log`.
