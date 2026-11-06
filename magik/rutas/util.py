"""Respuestas y lectura de datos comunes a todas las rutas."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Optional, Tuple

from flask import jsonify, request

from ..dominio.pedidos import pesos_a_centavos

FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def ok(**datos: Any) -> Any:
    return jsonify({"ok": True, **datos})


def error(mensaje: str, codigo: int = 400, **extra: Any) -> Any:
    return jsonify({"ok": False, "error": mensaje, **extra}), codigo


def cuerpo() -> Dict[str, Any]:
    d = request.get_json(silent=True)
    if isinstance(d, dict):
        return d
    return request.form.to_dict() if request.form else {}


def texto(d: Dict[str, Any], clave: str, maximo: int = 200) -> str:
    return str(d.get(clave) or "").strip()[:maximo]


def entero(d: Dict[str, Any], clave: str, defecto: Optional[int] = None) -> Optional[int]:
    try:
        v = d.get(clave)
        return defecto if v in (None, "") else int(float(str(v).replace(",", ".")))
    except (TypeError, ValueError):
        return defecto


def numero(d: Dict[str, Any], clave: str, defecto: float = 0.0) -> float:
    try:
        v = d.get(clave)
        return defecto if v in (None, "") else float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return defecto


def pesos(d: Dict[str, Any], clave: str, defecto: int = 0, negativo: bool = False) -> int:
    """Lee un importe en pesos ('1.500', '1500,50') y lo devuelve en centavos."""
    return pesos_a_centavos(d.get(clave), defecto, negativo)


def bandera(d: Dict[str, Any], clave: str, defecto: bool = False) -> bool:
    v = d.get(clave)
    if v is None:
        return defecto
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "si", "sí", "on", "yes"}
    return bool(v)


def fecha(v: Any, defecto: Optional[str] = None) -> Optional[str]:
    s = str(v or "").strip()[:10]
    if FECHA.match(s):
        try:
            date.fromisoformat(s)
            return s
        except ValueError:
            return defecto
    return defecto


def periodo() -> Tuple[str, str]:
    """?desde=YYYY-MM-DD&hasta=YYYY-MM-DD (vacío = sin límite)."""
    desde = fecha(request.args.get("desde"), "0000-01-01") or "0000-01-01"
    hasta = fecha(request.args.get("hasta"), "9999-12-31") or "9999-12-31"
    if desde > hasta:
        desde, hasta = hasta, desde
    return desde, hasta
