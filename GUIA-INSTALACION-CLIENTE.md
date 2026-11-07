# Instalar Magik en una computadora

Sirve para una **PC nueva** y para **reinstalar** en la del local. Reinstalar no borra
los datos.

**Requisitos:** Windows 10 u 11 de 64 bits. No hace falta instalar Python ni nada más:
el instalador trae todo.

---

## 1) Descargar el instalador

<https://github.com/luchoberaza/gestorpedidos-magikburger/releases/download/instalador/Instalar-Magik.exe>

(Es siempre el mismo link; descarga la última versión del instalador.)

## 2) Ejecutarlo

1. Doble clic en **`Instalar-Magik.exe`**.
2. Si Windows muestra el aviso azul **"Windows protegió tu PC"**, tocá
   **Más información → Ejecutar de todas formas**. Es lo único que hay que tocar: el
   instalador no pregunta nada.
3. Se instala, crea el ícono **MagikBurger Launcher** en el escritorio y **abre Magik
   solo** ("Abriendo…" y enseguida la caja).

La primera vez conviene tener internet: Magik baja la última versión al abrir. Sin
internet abre igual, con la versión que trae el instalador, y se actualiza la próxima vez
que haya conexión.

## 3) Entrar

- **PC nueva:** arranca con la base vacía. La caja entra sin contraseña; el usuario
  **admin** entra con la contraseña de administrador de siempre.
- **Reinstalación:** sigue todo como estaba (pedidos, productos, usuarios, configuración).

## 4) Pasar los datos desde otra PC (si hace falta)

1. En la PC vieja: **Configuración → Respaldo → Descargar respaldo ahora**. Se guarda un
   archivo `.db`; llevalo en un pendrive.
2. En la PC nueva: **Configuración → Respaldo → Restaurar desde archivo** y elegí ese
   archivo. Antes de reemplazar, Magik guarda un respaldo automático de lo que había.

> Si la PC vieja no abre Magik, el archivo está en
> `%APPDATA%\magikburger-launcher\data\magikburger.db` (Windows + R, pegar la ruta sin el
> nombre del archivo, Enter).

Si los datos vienen de *Caja Hamburguesería*: **Configuración → Importar de Caja**.

## 5) Configurar la impresora

**Configuración → Ticket e impresora**: elegir la térmica y tocar la prueba.

---

## Dónde queda cada cosa

| Qué | Dónde |
|---|---|
| El programa | `%LOCALAPPDATA%\Programs\magikburger-launcher` |
| La base, los respaldos y los reportes | `%APPDATA%\magikburger-launcher\data` |
| El registro de cada arranque (para diagnosticar) | `%APPDATA%\magikburger-launcher\launcher.log` |

Reinstalar o desinstalar **no toca** la carpeta `data`.

## Si algo sale mal

- Cerrá Magik y volvé a ejecutar `Instalar-Magik.exe`: repara la instalación sin tocar
  los datos.
- El `launcher.log` dice qué pasó en cada arranque.

---

## Para quien programa: volver a armar el instalador

Solo hace falta cuando cambia `launcher/main.js` o `requirements.txt`. El resto (código,
interfaz) llega solo por la actualización automática (ver `ACTUALIZACIONES.md`).

```
launcher\build_launcher.bat              (arma launcher\dist\Instalar-Magik.exe)
launcher\build_launcher.bat -Publicar    (arma y lo sube al link de arriba)
```

Necesita Node.js y el `.venv` del proyecto (o un Python con pip). Arma un Python embebido
3.13 con las dependencias de `requirements.txt`, verifica su firma y lo mete en el
instalador. Para publicar hace falta `gh` con sesión iniciada.
