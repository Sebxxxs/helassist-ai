"""Convierte los resultados (dicts) del servicio en mensajes de texto para Telegram."""
from __future__ import annotations

from typing import Any

from .parser import fmt_cop

MESES_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
            "septiembre", "octubre", "noviembre", "diciembre"]
REP_ES = {"diario": "todos los días", "semanal": "cada semana", "mensual": "cada mes"}


def rep_texto(rep: str) -> str:
    if rep.startswith("intervalo:"):
        mins = int(rep.split(":")[1])
        if mins == 60:
            return "cada hora"
        if mins % 60 == 0:
            return f"cada {mins // 60} horas"
        return f"cada {mins} minutos"
    if rep.startswith("mensual:"):
        return f"cada mes (el {rep.split(':')[1]}, o el último día si el mes es más corto)"
    return REP_ES.get(rep, rep)


def _fecha_hora(iso: str) -> str:
    fecha, hora = iso.split("T")
    return f"{fecha} a las {hora}"


def movimiento_registrado(r: dict[str, Any]) -> str:
    icono, palabra = ("💰", "ingreso") if r["tipo"] == "ingreso" else ("💸", "gasto")
    texto = f"{icono} Registrado: {palabra} de {fmt_cop(r['monto'])} en {r['categoria']} ({r['fecha']})."
    return texto + (f"\n{r['alerta']}" if r.get("alerta") else "")


def movimientos_registrados(rs: list[dict[str, Any]]) -> str:
    """Varios movimientos de un mismo mensaje."""
    if len(rs) == 1:
        return movimiento_registrado(rs[0])
    lineas = [f"✅ Registré {len(rs)} movimientos:"]
    for r in rs:
        signo = "➕" if r["tipo"] == "ingreso" else "➖"
        lineas.append(f"{signo} {fmt_cop(r['monto'])} · {r['categoria']} · {r['fecha']}")
    lineas += [r["alerta"] for r in rs if r.get("alerta")]
    return "\n".join(lineas)


def lista_categorias(d: dict[str, int], signo: str) -> list[str]:
    return [f"  {signo} {cat}: {fmt_cop(total)}" for cat, total in d.items()]


def resumen(r: dict[str, Any], periodo: str | None = None) -> str:
    if not r["ingresos"] and not r["gastos"]:
        return (f"📊 No tienes movimientos {periodo}." if periodo
                else "📊 Aún no tienes movimientos este período. Prueba: «Gasté 20 mil en comida».")
    lineas = [f"📊 Resumen financiero{' — ' + periodo if periodo else ''}"]
    if r["ingresos_por_categoria"]:
        lineas += ["Ingresos:"] + lista_categorias(r["ingresos_por_categoria"], "➕")
    if r["gastos_por_categoria"]:
        lineas += ["Gastos:"] + lista_categorias(r["gastos_por_categoria"], "➖")
    lineas += ["", f"Total ingresos: {fmt_cop(r['ingresos'])}", f"Total gastos: {fmt_cop(r['gastos'])}",
               f"Balance: {fmt_cop(r['balance'])}"]
    return "\n".join(lineas)


def analisis(a: dict[str, Any]) -> str:
    lineas = ["📝 Análisis financiero del mes",
              f"Ingresos: {fmt_cop(a['ingresos'])}", f"Gastos: {fmt_cop(a['gastos'])}"]
    if a["balance"] >= 0:
        lineas.append(f"Estás ahorrando {fmt_cop(a['balance'])}.")
    else:
        lineas.append(f"Te hacen falta {fmt_cop(-a['balance'])}.")
    if a["ratio"] is not None:
        lineas.append(f"Gastas el {a['ratio'] * 100:.0f} % de lo que ganas.")
    if a["categoria_mayor_gasto"]:
        c = a["categoria_mayor_gasto"]
        lineas.append(f"Tu mayor gasto: {c['categoria']} ({fmt_cop(c['total'])}).")
    lineas += ["", a["mensaje"]]
    return "\n".join(lineas)


def reporte(r: dict[str, Any]) -> str:
    anio, mes = r["mes"].split("-")
    return "\n".join([
        f"📆 Reporte mensual ({MESES_ES[int(mes)]} {anio})",
        f"Ingresos: {fmt_cop(r['ingresos'])}",
        f"Gastos: {fmt_cop(r['gastos'])}",
        f"Balance: {fmt_cop(r['balance'])}",
        "", r["analisis"]["mensaje"],
    ])


def movimientos(r: dict[str, Any], titulo: str = "🧾 Últimos movimientos:") -> str:
    if not r["ultimos_movimientos"]:
        return "No tienes movimientos registrados."
    lineas = [titulo]
    for m in r["ultimos_movimientos"]:
        signo = "➕" if m["tipo"] == "ingreso" else "➖"
        lineas.append(f"{signo} {m['fecha']} · {m['categoria']}: {fmt_cop(m['monto'])}")
    return "\n".join(lineas)


def consulta(r: dict[str, Any], d: dict[str, Any]) -> str:
    """Respuesta a «¿cuánto gasté en comida este mes?» o «ver mis ingresos»."""
    tipo, cat, per = d.get("tipo"), d.get("categoria"), d.get("periodo")
    palabra = {"gasto": "gastos", "ingreso": "ingresos"}.get(tipo, "movimientos")
    de_cat = f" en {cat}" if cat else ""
    cuando = f" {per}" if per else ""
    if d.get("modo") == "total":
        n = r.get("filtro_cantidad", 0)
        if not n:
            return f"No encontré {palabra}{de_cat}{cuando}."
        verbo = "Gastaste" if tipo == "gasto" else "Recibiste"
        return f"{'💸' if tipo == 'gasto' else '💰'} {verbo} {fmt_cop(r['filtro_total'])}{de_cat}{cuando} ({n} movimiento{'s' if n != 1 else ''})."
    if not r["ultimos_movimientos"]:
        return f"No encontré {palabra}{de_cat}{cuando}."
    titulo = f"🧾 {palabra.capitalize()}{de_cat}{cuando}:"
    if "filtro_total" in r:
        titulo += f" total {fmt_cop(r['filtro_total'])}"
    return movimientos(r, titulo)


def presupuestos(r: dict[str, Any]) -> str:
    if not r["presupuestos"]:
        return ("💼 Aún no tienes presupuestos. Un presupuesto es un tope mensual por categoría y te aviso "
                "cuando te acercas.\nEscribe, por ejemplo: «presupuesto de comida 500 mil».")
    lineas = [f"💼 Presupuestos de {MESES_ES[int(r['mes'][5:])]}:"]
    for p in r["presupuestos"]:
        icono = "🚨" if p["porcentaje"] >= 100 else "⚠️" if p["porcentaje"] >= 80 else "✅"
        resto = (f"te quedan {fmt_cop(p['restante'])}" if p["restante"] >= 0
                 else f"te pasaste por {fmt_cop(-p['restante'])}")
        lineas.append(f"{icono} {p['categoria']}: {fmt_cop(p['gastado'])} de {fmt_cop(p['limite'])} "
                      f"({p['porcentaje']} %) — {resto}")
    return "\n".join(lineas)


def tareas(r: dict[str, Any]) -> str:
    if not r["tareas"]:
        return "🎉 No tienes tareas pendientes."
    lineas = ["📋 Tareas pendientes:"]
    for t in r["tareas"]:
        vence = f" (vence: {t['fecha']})" if t["fecha"] else " (sin fecha)"
        lineas.append(f"⏳ ID {t['num']} - {t['descripcion']}{vence}")
    lineas.append("\nToca ✅ para completar una, o escribe «completar tarea 1».")
    return "\n".join(lineas)


def recordatorios(r: dict[str, Any]) -> str:
    if not r["recordatorios"]:
        return "No tienes recordatorios pendientes."
    lineas = ["📌 Recordatorios pendientes:"]
    for x in r["recordatorios"]:
        rep = f" · {rep_texto(x['repeticion'])}" if x["repeticion"] else ""
        lineas.append(f"• ID {x['num']} - {x['mensaje']} - {_fecha_hora(x['fecha_hora'])}{rep}")
    return "\n".join(lineas)


def recordatorio_creado(r: dict[str, Any]) -> str:
    rep = f" (se repite {rep_texto(r['repeticion'])})" if r["repeticion"] else ""
    return f"⏰ Listo, te recordaré «{r['mensaje']}» el {_fecha_hora(r['fecha_hora'])}{rep}."
