"""Autorización de datos y límite diario dentro del bot (requiere python-telegram-bot)."""
import asyncio
from dataclasses import replace

import pytest

pytest.importorskip("telegram")

from tests.test_ai_y_bot import (  # noqa: E402
    ClienteFalso, contexto, escribir, hacer_bot, hacer_update, resp, respuestas, texto, tocar,
)


def test_sin_autorizacion_no_se_guarda_nada(cfg):
    bot, ctx = hacer_bot(cfg, aceptado=False), contexto()
    u, m = hacer_update("Gasté 20 mil en comida")
    asyncio.run(bot.on_text(u, ctx))
    assert "Ley 1581" in respuestas(m)[0]
    assert bot.servicio.consultar_finanzas(1)["gastos"] == 0


def test_aceptar_guarda_la_autorizacion_y_da_la_bienvenida(cfg):
    bot, ctx = hacer_bot(cfg, aceptado=False), contexto()
    r = tocar(bot, ctx, "priv:si")
    assert "Guardé tu autorización" in r[0]
    assert bot.db.acepto_privacidad(1)
    u, m = hacer_update("Gasté 20 mil en comida")
    asyncio.run(bot.on_text(u, ctx))
    assert bot.servicio.consultar_finanzas(1)["gastos"] == 20000


def test_no_aceptar_deja_el_bot_bloqueado(cfg):
    bot, ctx = hacer_bot(cfg, aceptado=False), contexto()
    assert "Sin tu autorización" in tocar(bot, ctx, "priv:no")[0]
    assert not bot.db.acepto_privacidad(1)
    assert "Ley 1581" in tocar(bot, ctx, "menu:resumen")[0]


def test_aviso_menciona_a_anthropic_solo_si_hay_ia(cfg):
    sin_ia = hacer_bot(cfg, aceptado=False)
    assert "Anthropic" not in tocar(sin_ia, contexto(), "menu:resumen")[0]
    con_ia = hacer_bot(cfg, ClienteFalso([]), aceptado=False)
    assert "Anthropic" in tocar(con_ia, contexto(), "menu:resumen")[0]


def test_limite_diario_por_usuario(cfg):
    cliente = ClienteFalso([resp("end_turn", texto("Respuesta 1")), resp("end_turn", texto("Respuesta 2"))])
    bot, ctx = hacer_bot(replace(cfg, llm_max_por_dia=1), cliente), contexto()
    assert escribir(bot, ctx, "cuéntame un chiste") == ["Respuesta 1"]
    assert "Hoy ya usaste tus 1 mensajes" in escribir(bot, ctx, "otro chiste")[0]
    assert len(cliente.peticiones) == 1  # el segundo mensaje no llegó a la API
    assert "Registrado" in " ".join(escribir(bot, ctx, "Gasté 5 mil en comida"))  # las reglas siguen


def test_limite_diario_total(cfg):
    cliente = ClienteFalso([resp("end_turn", texto("Hola"))])
    bot, ctx = hacer_bot(replace(cfg, llm_max_dia_total=1), cliente), contexto()
    escribir(bot, ctx, "cuéntame un chiste", uid=1)
    assert "límite de uso de hoy" in escribir(bot, ctx, "cuéntame otro", uid=2)[0]
