"""Cálculos financieros puros (sin Telegram ni IA)."""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any

from .db import Database


def rango_mes(hoy: date, mes: str | None = None) -> tuple[str, str]:
    """('2025-04-01', '2025-04-30') del mes pedido (formato 'YYYY-MM') o del mes actual."""
    if mes:
        anio, m = (int(x) for x in mes.split("-"))
    else:
        anio, m = hoy.year, hoy.month
    return f"{anio:04d}-{m:02d}-01", f"{anio:04d}-{m:02d}-{monthrange(anio, m)[1]:02d}"


def resumen(db: Database, user_id: int, desde: str | None, hasta: str | None) -> dict[str, Any]:
    ingresos: dict[str, int] = {}
    gastos: dict[str, int] = {}
    for fila in db.totales_por_categoria(user_id, desde, hasta):
        (ingresos if fila["tipo"] == "ingreso" else gastos)[fila["categoria"]] = fila["total"]
    ti, tg = sum(ingresos.values()), sum(gastos.values())
    return {
        "desde": desde,
        "hasta": hasta,
        "ingresos": ti,
        "gastos": tg,
        "balance": ti - tg,
        "ingresos_por_categoria": ingresos,
        "gastos_por_categoria": gastos,
    }


def analisis(res: dict[str, Any]) -> dict[str, Any]:
    """Evalúa la salud financiera con la regla de gasto/ingreso."""
    ti, tg = res["ingresos"], res["gastos"]
    top = next(iter(res["gastos_por_categoria"].items()), None)
    out: dict[str, Any] = {
        "ingresos": ti,
        "gastos": tg,
        "balance": ti - tg,
        "categoria_mayor_gasto": {"categoria": top[0], "total": top[1]} if top else None,
    }
    if ti == 0:
        out.update(ratio=None, nivel="sin_ingresos",
                   mensaje="Aún no tienes ingresos registrados este período, así que no puedo medir "
                           "cuánto de lo que ganas estás gastando. Registra uno, por ejemplo: "
                           "«Recibí 1.500.000 de salario».")
        return out
    ratio = tg / ti
    out["ratio"] = round(ratio, 3)
    if ratio > 1:
        out.update(nivel="deficit", mensaje="Estás gastando más de lo que ganas. Revisa tus gastos "
                   "más grandes y define qué puedes recortar o aplazar.")
    elif ratio > 0.8:
        out.update(nivel="alerta", mensaje="Estás gastando más del 80 % de tus ingresos; te queda "
                   "poco margen para ahorrar. Intenta reducir gastos no esenciales.")
    elif ratio > 0.5:
        out.update(nivel="equilibrado", mensaje="Tus gastos están equilibrados. Una guía común es "
                   "50 % necesidades, 30 % gustos y 20 % ahorro; aún puedes ahorrar un poco más.")
    else:
        out.update(nivel="excelente", mensaje="¡Muy bien! Gastas menos de la mitad de lo que ganas. "
                   "Considera destinar parte del saldo a una meta de ahorro.")
    return out


def reporte_mensual(db: Database, user_id: int, hoy: date, mes: str | None = None) -> dict[str, Any]:
    desde, hasta = rango_mes(hoy, mes)
    res = resumen(db, user_id, desde, hasta)
    res["mes"] = desde[:7]
    res["analisis"] = analisis(res)
    return res
