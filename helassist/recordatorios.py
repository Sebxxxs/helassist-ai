"""Planificador de recordatorios persistentes.

Los recordatorios viven en la base de datos. Al arrancar el bot se vuelven a
programar todos los pendientes, así que NO se pierden si el bot se reinicia
(la versión anterior los guardaba solo en memoria). Si el bot estuvo apagado
cuando debía sonar uno, se envía apenas vuelve a encenderse.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .db import Database
from .parser import siguiente_fecha

log = logging.getLogger(__name__)
Enviar = Callable[[int, str], Awaitable[None]]
REINTENTO_SEGUNDOS = 300


class Recordatorios:
    def __init__(self, db: Database, tz: ZoneInfo, enviar: Enviar):
        self.db, self.tz, self.enviar = db, tz, enviar
        self.scheduler = AsyncIOScheduler(timezone=tz)

    def iniciar(self) -> None:
        """Debe llamarse con el event loop ya en marcha (p. ej. en post_init del bot)."""
        if not self.scheduler.running:
            self.scheduler.start()
        pendientes = self.db.recordatorios()
        for rec in pendientes:
            self.programar(rec)
        log.info("Recordatorios pendientes reprogramados: %d", len(pendientes))

    def detener(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def programar(self, rec: dict[str, Any], retraso_segundos: int | None = None) -> None:
        ahora = datetime.now(self.tz)
        run = datetime.fromisoformat(rec["fecha_hora"]).replace(tzinfo=self.tz)
        if retraso_segundos is not None or run <= ahora:
            run = ahora + timedelta(seconds=retraso_segundos or 3)
        self.scheduler.add_job(
            self._disparar,
            "date",
            run_date=run,
            args=[rec["id"]],
            id=f"rec-{rec['id']}",
            replace_existing=True,
            misfire_grace_time=None,  # ejecutar aunque llegue tarde
        )

    def cancelar(self, rec_id: int) -> None:
        try:
            self.scheduler.remove_job(f"rec-{rec_id}")
        except JobLookupError:
            pass

    async def _disparar(self, rec_id: int) -> None:
        rec = self.db.get_recordatorio(rec_id)
        if not rec or rec["enviado"]:
            return
        try:
            await self.enviar(rec["chat_id"], f"⏰ Recordatorio: {rec['mensaje']}")
        except Exception:
            log.exception("No se pudo enviar el recordatorio %s; reintento en %ss", rec_id, REINTENTO_SEGUNDOS)
            self.programar(rec, retraso_segundos=REINTENTO_SEGUNDOS)
            return
        if rec["repeticion"]:
            ahora = datetime.now(self.tz).replace(tzinfo=None)
            nuevo = siguiente_fecha(datetime.fromisoformat(rec["fecha_hora"]), rec["repeticion"], ahora)
            self.db.reprogramar(rec_id, nuevo.strftime("%Y-%m-%dT%H:%M"))
            self.programar(self.db.get_recordatorio(rec_id))
        else:
            self.db.marcar_enviado(rec_id)
