import asyncio
from datetime import datetime, timedelta

from helassist.recordatorios import Recordatorios
from helassist.servicio import Servicio


def test_recordatorio_se_envia_y_se_marca(db, cfg):
    enviados = []

    async def enviar(chat_id, texto):
        enviados.append((chat_id, texto))

    async def escenario():
        rec = Recordatorios(db, cfg.tz, enviar)
        rec.iniciar()
        s = Servicio(db, cfg, rec)
        r = s.crear_recordatorio(1, 555, "pagar Netflix", s.ahora() + timedelta(seconds=1))
        assert r["ok"]
        await asyncio.sleep(5.5)
        rec.detener()

    asyncio.run(escenario())
    assert enviados == [(555, "⏰ Recordatorio: pagar Netflix")]
    assert db.recordatorios(1) == []  # ya no está pendiente


def test_pendientes_se_reprograman_al_reiniciar_y_los_vencidos_salen_de_inmediato(db, cfg):
    # Simula un recordatorio que debió sonar mientras el bot estaba apagado
    db.add_recordatorio(1, 777, "se pasó", (datetime.now(cfg.tz) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"), None)
    enviados = []

    async def enviar(chat_id, texto):
        enviados.append(chat_id)

    async def escenario():
        rec = Recordatorios(db, cfg.tz, enviar)
        rec.iniciar()
        await asyncio.sleep(5.5)
        rec.detener()

    asyncio.run(escenario())
    assert enviados == [777]


def test_recurrente_se_reprograma(db, cfg):
    async def enviar(chat_id, texto):
        pass

    async def escenario():
        rec = Recordatorios(db, cfg.tz, enviar)
        rec.iniciar()
        s = Servicio(db, cfg, rec)
        s.crear_recordatorio(1, 5, "gym", s.ahora() + timedelta(seconds=1), "semanal")
        await asyncio.sleep(5.5)
        rec.detener()

    asyncio.run(escenario())
    pendientes = db.recordatorios(1)
    assert len(pendientes) == 1
    assert datetime.fromisoformat(pendientes[0]["fecha_hora"]) > datetime.now() + timedelta(days=6)
