# Guía paso a paso — Instalar la versión nueva en el local (UNA sola vez)

Esta guía es para dejar instalada en la computadora del local la versión con
**actualización automática**. Después de hacer esto **una sola vez**, no hay que volver a
instalar nunca más: los cambios futuros llegan solos al abrir la app.

> Hay dos partes:
> - **PARTE A:** en TU computadora (la de programar). Genera el instalador.
> - **PARTE B:** en la computadora DEL LOCAL. Instala y rescata la base de datos.
>
> Hacelas en orden. Tomate tu tiempo; está todo explicado con lo que hay que escribir y
> dónde tocar.

---

## PARTE A — En tu computadora (generar el instalador)

### A.1) Subir el código (lo finalizamos juntos)
El repositorio donde vive la app necesita tener el código nuevo. El comando exacto te lo
voy a dar una vez que definamos el repo (hay un detalle a resolver). En general es:

```
git add -A
git commit -m "Features nuevas + auto-actualización"
git push
```

### A.2) Generar el instalador
1. Abrí la app **Símbolo del sistema** (o **PowerShell**): tocá el botón **Inicio** de
   Windows, escribí `cmd` y abrí "Símbolo del sistema".
2. Escribí esto y apretá Enter (cambiá la ruta si tu proyecto está en otro lado):
   ```
   cd "C:\Users\lbera\OneDrive\Desktop\Proyectos\Proyecto Magik\launcher"
   ```
3. La primera vez, instalá las herramientas (esperá a que termine):
   ```
   npm install
   ```
4. Generá el instalador:
   ```
   npm run dist
   ```
5. Cuando termine, el instalador queda en la carpeta:
   `...\Proyecto Magik\launcher\dist\`
   Buscá un archivo que se llame algo como **`MagikBurger-Launcher-Setup-1.0.1.exe`**.
6. Copiá ese `.exe` a un pendrive (o mandátelo a la PC del local como prefieras).

---

## PARTE B — En la computadora del local (instalar SIN perder datos)

> ⚠️ MUY IMPORTANTE: primero rescatamos la base de datos actual (donde están todos los
> pedidos y la configuración) para que no se pierda nada.

### B.1) Cerrar la app actual
1. Si la app de MagikBurger está abierta, **cerrala** (la X arriba a la derecha).

### B.2) Encontrar y copiar la base de datos actual (el rescate)
1. Apretá las teclas **Windows + R** (se abre una ventanita "Ejecutar").
2. Escribí exactamente esto y apretá Enter:
   ```
   %LOCALAPPDATA%\Programs
   ```
   (Se abre el explorador de archivos. Ahí suele estar instalada la app.)
3. Buscá una carpeta llamada **`MagikBurger Launcher`** (o parecida) y entrá.
4. Entrá a: **`resources`** → **`backend_app`**.
5. Ahí adentro vas a ver un archivo llamado **`magikburger.db`**.
6. **Copialo** (clic derecho → Copiar) y **pegalo en el Escritorio** (clic derecho en el
   Escritorio → Pegar). Ese es el rescate: NO lo borres.

> Si en `%LOCALAPPDATA%\Programs` no aparece la carpeta, probá también en
> `C:\Program Files\MagikBurger Launcher\resources\backend_app\`. El archivo a copiar
> siempre es **`magikburger.db`**.

### B.3) Instalar la versión nueva
1. Hacé doble clic en el instalador (`MagikBurger-Launcher-Setup-...exe`) que trajiste.
2. Seguí los pasos (Siguiente / Instalar). Si Windows muestra un aviso azul de
   "Windows protegió tu PC", tocá **Más información** → **Ejecutar de todas formas**.
3. Cuando termine, **abrí la app una vez** (que cargue) y después **cerrala**.
   (Esto crea la carpeta donde va a vivir la base de ahora en más.)

### B.4) Poner la base rescatada en su lugar definitivo
1. Apretá de nuevo **Windows + R**.
2. Escribí exactamente esto y apretá Enter:
   ```
   %APPDATA%\MagikBurger Launcher\data
   ```
   (Se abre una carpeta llamada **`data`**. Adentro va a haber un `magikburger.db`
   "vacío", el que creó la app recién.)
3. Copiá el **`magikburger.db` del Escritorio** (el rescatado en el paso B.2) y
   **pegalo dentro de esta carpeta `data`**, eligiendo **"Reemplazar el archivo de
   destino"** cuando pregunte.

### B.5) Abrir y verificar
1. Abrí la app de nuevo.
2. Fijate que estén **los pedidos y datos de siempre** (productos, repartidores, etc.).
   Si está todo: ¡listo! Quedó instalada y con sus datos reales.

---

## ¡Y eso es todo!

A partir de ahora, cada cambio que hagas y subas se actualiza solo en el local **la
próxima vez que abran la app**. No hay que volver a instalar nada ni tocar la base.

### Si algo sale mal
- La base rescatada del Escritorio sigue ahí: podés repetir el paso B.4.
- En `%APPDATA%\MagikBurger Launcher\` hay un archivo `launcher.log` que registra qué
  pasó en cada arranque (sirve para diagnosticar).
