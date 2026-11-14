"""Unidades de la materia prima y de las recetas (P-016).

La materia prima se guarda en la unidad en que se compra (la carne, por kg) y las
recetas se escriben en una unidad cómoda: lo que se compra por kg va en gramos, lo
que se compra por litro va en ml, y la carne de las hamburguesas va en hamburguesas
("120 g es una hamburguesa […] siempre la hamburguesa pesa 120 g exactos").

En la base las recetas quedan siempre en la unidad de compra (0,24 kg para una
doble), así el stock y el costo hacen la misma cuenta que antes.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, Optional

# El peso de la hamburguesa que dijo el local. Se usa como valor inicial para la
# carne; cada materia prima guarda el suyo (insumos.porcion_g).
PESO_HAMBURGUESA_G = 120.0

PESO = {"kg": 1000.0, "g": 1.0}
VOLUMEN = {"l": 1000.0, "ml": 1.0}

SINONIMOS = {
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogramo": "kg", "kilogramos": "kg",
    "g": "g", "gr": "g", "grs": "g", "gramo": "g", "gramos": "g",
    "l": "l", "lt": "l", "lts": "l", "litro": "l", "litros": "l",
    "ml": "ml", "cc": "ml", "mililitro": "ml", "mililitros": "ml",
    "u": "unidades", "un": "unidades", "uni": "unidades", "unid": "unidades", "unidad": "unidades",
    "unidades": "unidades",
}


def normalizar(texto: Any) -> str:
    """Para comparar nombres: sin tildes, sin mayúsculas y sin espacios de más."""
    s = unicodedata.normalize("NFKD", str(texto if texto is not None else ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", s.replace(".", " ")).strip()


def unidad_de_compra(texto: Any, permitidas: Iterable[str] = ()) -> Optional[str]:
    """'Kilos' → 'kg', 'litro' → 'l', 'fetas' → 'fetas' (si está en Configuración). Si no se entiende, None."""
    s = normalizar(texto)
    if not s:
        return None
    if s in SINONIMOS:
        return SINONIMOS[s]
    for u in permitidas:
        if normalizar(u) == s:
            return str(u)
    return None


def admite_hamburguesa(unidad: Any) -> bool:
    return str(unidad or "") in PESO


def es_carne(nombre: Any) -> bool:
    """Para proponer la carne con su peso de hamburguesa en la planilla (Inferido del nombre)."""
    n = normalizar(nombre)
    return n.startswith("carne") or n.startswith("medallon") or n.startswith("hamburguesa")


def unidad_receta(unidad: Any, porcion_g: Any = 0) -> Dict[str, Any]:
    """En qué se escribe la cantidad en una receta y cuánto es eso en la unidad de compra.

    factor: cantidad en la receta × factor = cantidad en la unidad de compra."""
    u = str(unidad or "unidades")
    peso = float(porcion_g or 0)
    if u in PESO and peso > 0:
        return {"singular": "hamburguesa", "plural": "hamburguesas", "factor": peso / PESO[u],
                "texto": f"hamburguesas de {_num(peso)} g", "hamburguesa": True}
    if u in PESO:
        return {"singular": "g", "plural": "g", "factor": 1.0 / PESO[u], "texto": "gramos", "hamburguesa": False}
    if u in VOLUMEN:
        return {"singular": "ml", "plural": "ml", "factor": 1.0 / VOLUMEN[u], "texto": "ml", "hamburguesa": False}
    if u == "unidades":
        return {"singular": "unidad", "plural": "unidades", "factor": 1.0, "texto": "unidades", "hamburguesa": False}
    return {"singular": singular(u), "plural": u, "factor": 1.0, "texto": u, "hamburguesa": False}


def singular(palabra: str) -> str:
    """'fetas' → 'feta', 'porciones' → 'porción', 'paquetes' → 'paquete' (para «1 feta»)."""
    p = str(palabra or "")
    if p.endswith("ones"):
        return p[:-4] + "ón"
    if p.endswith("dades"):
        return p[:-2]
    return p[:-1] if p.endswith("s") and len(p) > 2 else p


def a_compra(cantidad_receta: float, unidad: Any, porcion_g: Any = 0) -> float:
    return round(float(cantidad_receta) * unidad_receta(unidad, porcion_g)["factor"], 6)


def a_receta(cantidad_compra: float, unidad: Any, porcion_g: Any = 0) -> float:
    f = unidad_receta(unidad, porcion_g)["factor"]
    return round(float(cantidad_compra or 0) / f, 4) if f else 0.0


def conversion(vieja: Any, nueva: Any) -> Optional[float]:
    """Cuánto vale 1 de la unidad vieja en la nueva (kg → g = 1000). None si no se pueden convertir."""
    v, n = str(vieja or ""), str(nueva or "")
    if v == n:
        return 1.0
    for grupo in (PESO, VOLUMEN):
        if v in grupo and n in grupo:
            return grupo[v] / grupo[n]
    return None


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(round(float(x), 2)).replace(".", ",")
