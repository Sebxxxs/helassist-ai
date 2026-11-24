"""Lógica de negocio compartida por el bot (reglas) y el asistente IA (herramientas).

Cada método devuelve un dict serializable. Si algo falla, devuelve {"error": "..."}
en lugar de lanzar excepciones, para que el asistente pueda explicárselo al usuario.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any, Protocol

from . import finanzas
from .config import Config
from .db import Database
from .parser import MIN_INTERVALO, normalizar_categoria

MAX_MONTO = 100_000_000_000  # 100 mil millones: evita typos absurdos
REPETICIONES = {"diario", "semanal", "mensual"}
_RE_REP = re.compile(r"(?:mensual:(?:29|30|31)|intervalo:\d{1,5})")


def _cop(n: int) -> str:
    return "$" + f"{int(n):,}".replace(",", ".")


class Planificador(Protocol):
    def programar(self, rec: dict[str, Any]) -> None: ...
    def cancelar(self, rec_id: int) -> None: ...


class Servicio:
    def __init__(self, db: Database, cfg: Config, planificador: Planificador | None = None):
        self.db, self.cfg, self.planificador = db, cfg, planificador

    # ------------------------------------------------------------------ tiempo
    def ahora(self) -> datetime:
        """Hora local actual (sin zona horaria, para comparar con fechas guardadas)."""
        return datetime.now(self.cfg.tz).replace(tzinfo=None, microsecond=0)

    def hoy(self) -> date:
        return self.ahora().date()

    def hora_defecto(self) -> time:
        h, m = (int(x) for x in self.cfg.hora_recordatorio_defecto.split(":"))
        return time(h, m)

    # ------------------------------------------------------------- movimientos
    def registrar_movimiento(
        self,
        user_id: int,
        tipo: str,
        monto: Any,
        categoria: str | None = None,
        descripcion: str | None = None,
        fecha: str | None = None,
    ) -> dict[str, Any]:
        if tipo not in ("gasto", "ingreso"):
            return {"error": "El tipo debe ser 'gasto' o 'ingreso'."}
        try:
            monto = int(round(float(monto)))
        except (TypeError, ValueError):
            return {"error": "El monto no es un número válido."}
        if monto <= 0 or monto > MAX_MONTO:
            return {"error": "El monto debe ser mayor que 0 y razonable."}
        try:
            fecha = date.fromisoformat(fecha).isoformat() if fecha else self.hoy().isoformat()
        except ValueError:
            return {"error": "La fecha debe tener formato AAAA-MM-DD."}
        cat = normalizar_categoria(categoria)
        mov_id = self.db.add_movimiento(user_id, tipo, monto, cat, (descripcion or "")[:200], fecha)
        res = {"ok": True, "id": mov_id, "tipo": tipo, "monto": monto, "categoria": cat, "fecha": fecha}
        if tipo == "gasto" and (alerta := self._alerta_presupuesto(user_id, cat, fecha, monto)):
            res["alerta"] = alerta
        return res

    def _alerta_presupuesto(self, user_id: int, cat: str, fecha: str, monto: int) -> str | None:
        """Avisa al cruzar el 80 % y el 100 % del presupuesto mensual de la categoría."""
        limite = self.db.get_presupuesto(user_id, cat)
        if not limite:
            return None
        desde, hasta = finanzas.rango_mes(date.fromisoformat(fecha))
        total = self.db.total_gastado(user_id, cat, desde, hasta)
        antes = total - monto
        if total >= limite:
            if antes < limite:
                return f"🚨 Superaste tu presupuesto de {cat}: llevas {_cop(total)} de {_cop(limite)}."
            return f"🚨 Sigues por encima del presupuesto de {cat}: {_cop(total)} de {_cop(limite)}."
        if total * 100 >= limite * 80 and antes * 100 < limite * 80:
            return (f"⚠️ Ya llevas el {total * 100 // limite} % de tu presupuesto de {cat} "
                    f"({_cop(total)} de {_cop(limite)}). Te quedan {_cop(limite - total)}.")
        return None

    def deshacer(self, user_id: int) -> dict[str, Any]:
        ult = self.db.ultimo_movimiento(user_id)
        if not ult:
            return {"error": "No tienes movimientos para deshacer."}
        self.db.borrar_movimiento(user_id, ult["id"])
        return {"ok": True, "eliminado": ult}

    def consultar_finanzas(
        self,
        user_id: int,
        desde: str | None = None,
        hasta: str | None = None,
        tipo: str | None = None,
        categoria: str | None = None,
    ) -> dict[str, Any]:
        try:
            for f in (desde, hasta):
                if f:
                    date.fromisoformat(f)
        except ValueError:
            return {"error": "Las fechas deben tener formato AAAA-MM-DD."}
        cat = normalizar_categoria(categoria) if categoria else None
        res = finanzas.resumen(self.db, user_id, desde, hasta)
        movs = self.db.movimientos(user_id, tipo, desde, hasta, cat, limite=15)
        res["ultimos_movimientos"] = [
            {k: m[k] for k in ("id", "tipo", "monto", "categoria", "descripcion", "fecha")} for m in movs
        ]
        if cat or tipo:
            todos = self.db.movimientos(user_id, tipo, desde, hasta, cat)
            res["filtro_total"] = sum(m["monto"] for m in todos)
            res["filtro_cantidad"] = len(todos)
        return res

    def resumen_periodo(self, user_id: int, desde: str | None, hasta: str | None) -> dict[str, Any]:
        try:
            for f in (desde, hasta):
                if f:
                    date.fromisoformat(f)
        except ValueError:
            return {"error": "Las fechas deben tener formato AAAA-MM-DD."}
        return finanzas.resumen(self.db, user_id, desde, hasta)

    def resumen_mes(self, user_id: int, mes: str | None = None) -> dict[str, Any]:
        desde, hasta = finanzas.rango_mes(self.hoy(), mes)
        return finanzas.resumen(self.db, user_id, desde, hasta)

    def analisis(self, user_id: int) -> dict[str, Any]:
        return finanzas.analisis(self.resumen_mes(user_id))

    def reporte_mensual(self, user_id: int, mes: str | None = None) -> dict[str, Any]:
        return finanzas.reporte_mensual(self.db, user_id, self.hoy(), mes)

    # ------------------------------------------------------------------ tareas
    def crear_tarea(self, user_id: int, descripcion: str, fecha: str | None = None) -> dict[str, Any]:
        descripcion = (descripcion or "").strip()
        if not descripcion:
            return {"error": "La tarea necesita una descripción."}
        try:
            fecha = date.fromisoformat(fecha).isoformat() if fecha else None
        except ValueError:
            return {"error": "La fecha debe tener formato AAAA-MM-DD."}
        num = self.db.add_tarea(user_id, descripcion[:200], fecha)
        return {"ok": True, "num": num, "descripcion": descripcion[:200], "fecha": fecha}

    def listar_tareas(self, user_id: int) -> dict[str, Any]:
        return {"tareas": [
            {"num": t["num"], "descripcion": t["descripcion"], "fecha": t["fecha"]}
            for t in self.db.tareas(user_id)
        ]}

    def completar_tarea(self, user_id: int, num: Any) -> dict[str, Any]:
        try:
            num = int(num)
        except (TypeError, ValueError):
            return {"error": "El número de tarea no es válido."}
        tarea = self.db.get_tarea(user_id, num)
        if not tarea:
            return {"error": f"No encontré la tarea {num}."}
        if tarea["completada"]:
            return {"error": f"La tarea {num} ya estaba completada."}
        self.db.completar_tarea(user_id, num)
        return {"ok": True, "num": num, "descripcion": tarea["descripcion"]}

    # ----------------------------------------------------------- recordatorios
    def crear_recordatorio(
        self,
        user_id: int,
        chat_id: int,
        mensaje: str,
        fecha_hora: datetime,
        repeticion: str | None = None,
    ) -> dict[str, Any]:
        mensaje = (mensaje or "").strip()
        if not mensaje:
            return {"error": "El recordatorio necesita un mensaje."}
        if repeticion in ("ninguna", "", "ninguno"):
            repeticion = None
        if repeticion and repeticion not in REPETICIONES and not _RE_REP.fullmatch(repeticion):
            return {"error": "La repetición debe ser diario, semanal o mensual (o cada cierto número de minutos)."}
        if repeticion and repeticion.startswith("intervalo:") and int(repeticion[10:]) < MIN_INTERVALO:
            return {"error": f"Lo mínimo es repetir cada {MIN_INTERVALO} minutos."}
        if repeticion == "mensual" and fecha_hora.day > 28:  # recuerda el día original en meses cortos
            repeticion = f"mensual:{fecha_hora.day}"
        if fecha_hora <= self.ahora():
            return {"error": "Esa fecha y hora ya pasaron."}
        rec = self.db.add_recordatorio(
            user_id, chat_id, mensaje[:200], fecha_hora.strftime("%Y-%m-%dT%H:%M"), repeticion
        )
        if self.planificador:
            self.planificador.programar(rec)
        return {"ok": True, "num": rec["num"], "mensaje": rec["mensaje"],
                "fecha_hora": rec["fecha_hora"], "repeticion": rec["repeticion"]}

    def crear_recordatorio_texto(
        self, user_id: int, chat_id: int, mensaje: str, fecha: str, hora: str | None, repeticion: str | None,
        cada_minutos: Any = None,
    ) -> dict[str, Any]:
        """Variante para el asistente IA: fecha 'AAAA-MM-DD' y hora 'HH:MM' como texto."""
        try:
            d = date.fromisoformat(fecha)
            if hora:
                h, m = (int(x) for x in hora.split(":")[:2])
                t = time(h, m)
            else:
                t = self.hora_defecto()
        except (ValueError, TypeError):
            return {"error": "Usa fecha AAAA-MM-DD y hora HH:MM (24 horas)."}
        if cada_minutos:
            try:
                repeticion = f"intervalo:{int(cada_minutos)}"
            except (TypeError, ValueError):
                return {"error": "cada_minutos debe ser un número."}
        return self.crear_recordatorio(user_id, chat_id, mensaje, datetime.combine(d, t), repeticion)

    def listar_recordatorios(self, user_id: int) -> dict[str, Any]:
        return {"recordatorios": [
            {"num": r["num"], "mensaje": r["mensaje"], "fecha_hora": r["fecha_hora"], "repeticion": r["repeticion"]}
            for r in self.db.recordatorios(user_id)
        ]}

    def borrar_recordatorio(self, user_id: int, num: Any) -> dict[str, Any]:
        try:
            num = int(num)
        except (TypeError, ValueError):
            return {"error": "El número de recordatorio no es válido."}
        rec = self.db.borrar_recordatorio(user_id, num)
        if not rec:
            return {"error": f"No encontré el recordatorio {num}."}
        if self.planificador:
            self.planificador.cancelar(rec["id"])
        return {"ok": True, "num": num, "mensaje": rec["mensaje"]}

    # ------------------------------------------------------------ presupuestos
    def definir_presupuesto(self, user_id: int, categoria: str | None, monto: Any) -> dict[str, Any]:
        cat = normalizar_categoria(categoria)
        if cat == "otros" and not (categoria or "").strip():
            return {"error": "Dime para qué categoría es el presupuesto."}
        try:
            monto = int(round(float(monto)))
        except (TypeError, ValueError):
            return {"error": "El monto no es un número válido."}
        if monto <= 0 or monto > MAX_MONTO:
            return {"error": "El presupuesto debe ser mayor que 0 y razonable."}
        self.db.set_presupuesto(user_id, cat, monto)
        gastado = self.db.total_gastado(user_id, cat, *finanzas.rango_mes(self.hoy()))
        return {"ok": True, "categoria": cat, "limite": monto, "gastado_este_mes": gastado}

    def listar_presupuestos(self, user_id: int) -> dict[str, Any]:
        desde, hasta = finanzas.rango_mes(self.hoy())
        filas = []
        for p in self.db.presupuestos(user_id):
            gastado = self.db.total_gastado(user_id, p["categoria"], desde, hasta)
            filas.append({
                "categoria": p["categoria"], "limite": p["monto"], "gastado": gastado,
                "restante": p["monto"] - gastado, "porcentaje": round(gastado * 100 / p["monto"]),
            })
        return {"mes": desde[:7], "presupuestos": filas}

    def borrar_presupuesto(self, user_id: int, categoria: str | None) -> dict[str, Any]:
        cat = normalizar_categoria(categoria)
        if not self.db.borrar_presupuesto(user_id, cat):
            return {"error": f"No tenías un presupuesto para {cat}."}
        return {"ok": True, "categoria": cat}

    # ------------------------------------------------------------------- reset
    def reiniciar(self, user_id: int) -> dict[str, Any]:
        if self.planificador:
            for r in self.db.recordatorios(user_id):
                self.planificador.cancelar(r["id"])
        return {"ok": True, "eliminados": self.db.reiniciar_usuario(user_id)}
