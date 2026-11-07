"""Magik — gestión de pedidos, caja y finanzas para hamburgueserías."""
from __future__ import annotations

import os
from datetime import timedelta

from flask import Flask, jsonify, redirect, render_template, request

VERSION = "2.2.0"


def create_app() -> Flask:
    import mimetypes

    # En algunas PCs con Windows el registro dice que .js es "text/plain" y el
    # navegador se niega a ejecutarlo: se fija acá para no depender de eso.
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")

    from . import config
    from .db import APP_DIR, carpeta, cerrar_db, conectar, migrar, respaldo_automatico
    from .rutas import admin, finanzas, inventario, operacion, sesion

    app = Flask(__name__, template_folder=os.path.join(APP_DIR, "templates"),
                static_folder=os.path.join(APP_DIR, "static"))

    migrar()
    conn = conectar()
    try:
        app.secret_key = config.secret_key(conn)
        auto = config.obtener(conn, "respaldo_auto")
        conservar = config.obtener(conn, "respaldos_a_conservar")
    finally:
        conn.close()
    app.permanent_session_lifetime = timedelta(days=30)
    app.config.update(SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=64 * 1024 * 1024,
                      SEND_FILE_MAX_AGE_DEFAULT=0, JSON_AS_ASCII=False)
    try:
        app.json.ensure_ascii = False  # Flask >= 2.2
    except AttributeError:
        pass

    # Respaldo al arrancar, una vez por día.
    if auto:
        try:
            d = carpeta("respaldos")
            from datetime import date
            hoy = date.today().strftime("%Y%m%d")
            if not any(f.startswith(f"inicio_{hoy}") for f in os.listdir(d)):
                respaldo_automatico("inicio", conservar)
        except OSError:
            pass

    app.teardown_appcontext(cerrar_db)
    for bp in (sesion.bp, operacion.bp, finanzas.bp, inventario.bp, admin.bp):
        app.register_blueprint(bp)

    @app.get("/")
    def index():
        return render_template("index.html", version=VERSION)

    # Rutas viejas (accesos directos, marcadores): todo vive ahora en la app.
    @app.get("/home")
    @app.get("/admin")
    @app.get("/admin/<path:_resto>")
    def viejas(_resto: str = ""):
        return redirect("/")

    @app.after_request
    def sin_cache(resp):
        if request.path.startswith("/api/") or request.path == "/":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(404)
    def no_encontrado(_e):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "No encontrado."}), 404
        return redirect("/")

    @app.errorhandler(500)
    def error_interno(e):
        if request.path.startswith("/api/") or request.path.startswith("/orders/"):
            return jsonify({"ok": False, "error": f"Error inesperado del servidor: {e}"}), 500
        return f"Error inesperado: {e}", 500

    @app.errorhandler(413)
    def muy_grande(_e):
        return jsonify({"ok": False, "error": "El archivo es demasiado grande."}), 413

    return app
