"""Punto de entrada de Magik.

El launcher de escritorio carga este archivo y llama a `app.run(...)`, así que
`app` tiene que existir a nivel módulo. Todo el código vive en el paquete
`magik/`; acá solo se arma la aplicación.

Uso directo:  python app.py   (abre en http://127.0.0.1:5000)
"""
import os
import sys

_AQUI = os.path.dirname(os.path.abspath(__file__))
if _AQUI not in sys.path:
    sys.path.insert(0, _AQUI)

from magik import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("MAGIK_HOST", "127.0.0.1"), port=int(os.environ.get("MAGIK_PORT", "5000")),
            debug=False)
