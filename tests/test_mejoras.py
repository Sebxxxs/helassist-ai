"""Pruebas de la ronda de mejoras: datos mal guardados, frases comunes, presupuestos y respaldo."""
import asyncio
import os
from datetime import date, datetime, timedelta

import pytest

from helassist.formato import rep_texto
from helassist.parser import (
    buscar_periodo,
    detectar_intencion,
    parse_movimientos,
    parse_recordatorio,
    plano,
    siguiente_fecha,
)
from helassist.recordatorios import Recordatorios
from tests.test_ai_y_bot import contexto, hacer_bot, hacer_update, respuestas

AHORA = datetime(2026, 11, 19, 10, 7)  # jueves
HOY = AHORA.date()


# ------------------------------------------------- 1) errores que guardaban datos mal
def test_varios_gastos_en_un_mensaje_se_guardan_todos():
    movs = parse_movimientos("gasté 20 mil en comida y 15 mil en transporte", HOY)
    assert [(m["monto"], m["categoria"]) for m in movs] == [(20000, "comida"), (15000, "transporte")]


def test_varios_movimientos_con_fecha_y_tipos_mezclados():
    movs = parse_movimientos("ayer gasté 10 mil en pizza y 5 mil en bus", HOY)
    assert {m["fecha"] for m in movs} == {"2026-11-18"} and len(movs) == 2
    mix = parse_movimientos("gasté 20 mil en comida y me pagaron 100 mil de salario", HOY)
    assert [m["tipo"] for m in mix] == ["gasto", "ingreso"]


def test_no_parte_montos_escritos_en_palabras():
    assert len(parse_movimientos("gasté dos mil y quinientos en dulces", HOY)) == 1
    assert len(parse_movimientos("gasté 20 mil en pizza y gaseosa", HOY)) == 1


def test_dia_de_la_semana_en_un_gasto_es_el_pasado():
    (m,) = parse_movimientos("el lunes gasté 30 mil en uber", HOY)
    assert m["fecha"] == "2026-11-16" and m["categoria"] == "transporte"


def test_fecha_futura_sin_anio_en_gasto_es_del_anio_pasado():
    (m,) = parse_movimientos("gasté 20 mil en comida el 25/12", HOY)
    assert m["fecha"] == "2025-12-25"


def test_montos_diminutos_no_se_guardan():
    assert parse_movimientos("compré 2 pizzas", HOY) == []
    assert detectar_intencion("2 pizzas", AHORA) is None


def test_recibo_de_servicios_no_es_ingreso():
    d = detectar_intencion("recibo de la luz 80 mil", AHORA)
    assert d["tipo"] == "gasto" and d["categoria"] == "servicios"


def test_todos_los_lunes_es_semanal_y_el_mensaje_queda_limpio():
    r = parse_recordatorio("recuérdame ir al gym todos los lunes a las 6 am", AHORA)
    assert r["repeticion"] == "semanal" and r["mensaje"] == "ir al gym"
    assert r["fecha_hora"] == datetime(2026, 11, 23, 6, 0)


def test_semanal_hoy_mismo_si_la_hora_aun_no_paso():
    jueves = datetime(2026, 11, 19, 7, 0)
    r = parse_recordatorio("recuérdame estudiar todos los jueves a las 9 am", jueves)
    assert r["fecha_hora"] == datetime(2026, 11, 19, 9, 0)


@pytest.mark.parametrize("texto,intent,tipo,periodo", [
    ("ver mis ingresos", "consulta", "ingreso", None),
    ("gastos de la semana", "consulta", "gasto", "esta semana"),
    ("movimientos de ayer", "consulta", None, "ayer"),
    ("resumen de la semana", "resumen", None, "esta semana"),
    ("resumen de octubre", "resumen", None, "de octubre"),
])
def test_consultas_respetan_tipo_y_periodo(texto, intent, tipo, periodo):
    d = detectar_intencion(texto, AHORA)
    assert d["intent"] == intent and d.get("tipo") == tipo and d.get("periodo") == periodo


def test_periodos():
    assert buscar_periodo("esta semana", HOY)[:2] == ("2026-11-16", "2026-11-22")
    assert buscar_periodo("la semana pasada", HOY)[:2] == ("2026-11-09", "2026-11-15")
    assert buscar_periodo("el mes pasado", HOY)[:2] == ("2026-10-01", "2026-10-31")
    assert buscar_periodo("ultimos 7 dias", HOY)[:2] == ("2026-11-13", "2026-11-19")
    assert buscar_periodo("en diciembre", HOY)[:2] == ("2025-12-01", "2025-12-31")  # aún no llega: el anterior


# --------------------------------------------------------- 2) frases comunes
@pytest.mark.parametrize("texto,minutos", [
    ("recuérdame llamar a mamá en 30 minutos", 30), ("recuérdame sacar la ropa dentro de 2 horas", 120),
    ("avísame en media hora", 30), ("recuérdame algo en una hora y media", 90),
])
def test_recordatorios_relativos(texto, minutos):
    r = parse_recordatorio(texto, AHORA)
    assert r["fecha_hora"] == (AHORA + timedelta(minutes=minutos)).replace(second=0)


def test_recordatorio_cada_n_horas():
    r = parse_recordatorio("recuérdame tomar agua cada 2 horas", AHORA)
    assert r["repeticion"] == "intervalo:120" and r["mensaje"] == "tomar agua"
    assert r["fecha_hora"] == datetime(2026, 11, 19, 12, 7)
    assert rep_texto("intervalo:120") == "cada 2 horas" and rep_texto("intervalo:60") == "cada hora"


def test_intervalo_muy_corto_se_rechaza():
    assert detectar_intencion("recuérdame estirarme cada 2 minutos", AHORA)["intent"] == "recordatorio_error"


def test_alarma():
    d = detectar_intencion("pon una alarma a las 6 am", AHORA)
    assert d["intent"] == "recordatorio" and d["mensaje"] == "Alarma"
    assert d["fecha_hora"] == datetime(2026, 11, 20, 6, 0)
    assert detectar_intencion("cómo pongo una alarma", AHORA) is None  # es una pregunta


@pytest.mark.parametrize("texto,intent", [
    ("hola", "saludo"), ("buenas tardes", "saludo"), ("hola, cómo estás?", "saludo"), ("gracias!", "gracias"),
    ("muchas gracias", "gracias"), ("chao", "despedida"), ("qué puedes hacer", "ayuda"), ("ayuda", "ayuda"),
    ("tarea: comprar leche", "tarea"), ("tengo que pagar el arriendo el viernes", "tarea"),
    ("cuánto he gastado en comida este mes", "consulta"), ("cómo voy", "resumen"), ("balance", "resumen"),
    ("salario 1500000", "movimiento"), ("netflix 30000", "movimiento"), ("me llegó la quincena, 700 mil", "movimiento"),
    ("me cayeron 200 mil de una venta", "movimiento"), ("tanqueé la moto 20 mil", "movimiento"),
])
def test_frases_comunes(texto, intent):
    assert detectar_intencion(texto, AHORA)["intent"] == intent


def test_tengo_que_con_plata_no_es_tarea():
    assert detectar_intencion("debo 50 mil a juan", AHORA) is None


def test_ingreso_sin_verbo_es_ingreso():
    assert detectar_intencion("salario 1500000", AHORA)["tipo"] == "ingreso"
    assert detectar_intencion("uber 15 mil", AHORA)["tipo"] == "gasto"


# ----------------------------------------------------------- 3) cambios propuestos
def test_dia_ancla_mensual_en_meses_cortos():
    r = parse_recordatorio("recuérdame pagar arriendo el 31 de cada mes", AHORA)
    assert r["repeticion"] == "mensual:31" and r["fecha_hora"] == datetime(2026, 11, 30, 9, 0)
    sig = siguiente_fecha(r["fecha_hora"], "mensual:31", AHORA)
    assert sig == datetime(2026, 12, 31, 9, 0)
    assert siguiente_fecha(datetime(2027, 2, 28, 9, 0), "mensual:31", datetime(2027, 3, 1)) == datetime(2027, 3, 31, 9, 0)
    assert siguiente_fecha(datetime(2026, 11, 6, 9, 0), "intervalo:120", datetime(2026, 11, 6, 9, 30)) == datetime(2026, 11, 6, 11, 0)


def test_servicio_normaliza_mensual_con_dia_alto(servicio):
    r = servicio.crear_recordatorio(1, 1, "x", servicio.ahora() + timedelta(days=40, hours=1), "mensual")
    assert r["ok"]
    fecha = datetime.fromisoformat(r["fecha_hora"])
    assert (r["repeticion"] == f"mensual:{fecha.day}") == (fecha.day > 28)
    assert servicio.crear_recordatorio(1, 1, "y", servicio.ahora() + timedelta(days=1), "intervalo:1")["error"]


def test_presupuesto_avisa_al_80_y_al_100(servicio):
    assert servicio.definir_presupuesto(1, "comida", 100000)["ok"]
    r = servicio.registrar_movimiento(1, "gasto", 50000, "comida")
    assert "alerta" not in r
    r = servicio.registrar_movimiento(1, "gasto", 35000, "comida")
    assert "85 %" in r["alerta"] and "⚠️" in r["alerta"]
    r = servicio.registrar_movimiento(1, "gasto", 5000, "comida")
    assert "alerta" not in r  # sigue entre 80 y 100: no repite el aviso
    r = servicio.registrar_movimiento(1, "gasto", 20000, "comida")
    assert "Superaste" in r["alerta"]
    assert "Sigues por encima" in servicio.registrar_movimiento(1, "gasto", 1000, "comida")["alerta"]
    assert "alerta" not in servicio.registrar_movimiento(1, "gasto", 99999, "transporte")
    lista = servicio.listar_presupuestos(1)["presupuestos"][0]
    assert lista["categoria"] == "comida" and lista["gastado"] == 111000 and lista["restante"] < 0


def test_presupuestos_por_usuario_y_borrado(servicio):
    servicio.definir_presupuesto(1, "comida", 100000)
    assert servicio.listar_presupuestos(2)["presupuestos"] == []
    assert servicio.borrar_presupuesto(1, "comida")["ok"] and "error" in servicio.borrar_presupuesto(1, "comida")
    assert "error" in servicio.definir_presupuesto(1, "comida", -5)
    servicio.definir_presupuesto(1, "ropa", 1000)
    servicio.reiniciar(1)
    assert servicio.listar_presupuestos(1)["presupuestos"] == []


def test_respaldo_consistente_y_rotacion(db, tmp_path):
    import sqlite3

    db.add_movimiento(1, "gasto", 1000, "comida", None, "2026-11-19")
    carpeta = tmp_path / "bk"
    carpeta.mkdir()
    for dia in range(1, 11):  # 10 respaldos viejos
        (carpeta / f"helassist-2026010{dia}.db" if dia < 10 else carpeta / "helassist-20260110.db").write_bytes(b"x")
    destino = db.respaldar(str(carpeta), conservar=3)
    assert os.path.exists(destino)
    assert len(list(carpeta.glob("helassist-*.db"))) == 3
    n = sqlite3.connect(destino).execute("SELECT COUNT(*) FROM movimientos").fetchone()[0]
    assert n == 1
    assert not list(carpeta.glob("*.tmp"))


def test_recordatorio_por_intervalo_se_reprograma(db, cfg):
    enviados = []

    async def enviar(chat_id, texto):
        enviados.append(texto)

    async def escenario():
        rec = Recordatorios(db, cfg.tz, enviar)
        rec.iniciar()
        ahora = datetime.now(cfg.tz).replace(tzinfo=None, microsecond=0)
        r = db.add_recordatorio(1, 5, "agua", (ahora + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M"), "intervalo:60")
        rec.programar(r)
        await asyncio.sleep(5.5)
        rec.detener()

    asyncio.run(escenario())
    assert enviados == ["⏰ Recordatorio: agua"]
    pendiente = db.recordatorios(1)
    assert len(pendiente) == 1 and datetime.fromisoformat(pendiente[0]["fecha_hora"]) > datetime.now()


# --------------------------------------------------------------- bot de punta a punta
def _decir(bot, ctx, texto, uid=1):
    u, m = hacer_update(texto, uid)
    asyncio.run(bot._procesar(u, ctx, texto))
    return " ".join(respuestas(m)), m


def test_bot_guarda_todos_los_gastos_y_los_consulta(cfg):
    bot = hacer_bot(cfg)
    ctx = contexto()
    out, _ = _decir(bot, ctx, "gasté 20 mil en comida y 15 mil en transporte")
    assert "Registré 2 movimientos" in out
    assert len(bot.db.movimientos(1)) == 2
    out, _ = _decir(bot, ctx, "cuánto he gastado en comida este mes")
    assert "$20.000 en comida" in out
    _decir(bot, ctx, "salario 1500000")
    out, _ = _decir(bot, ctx, "ver mis ingresos")
    assert "salario" in out and "comida" not in out  # antes mostraba los gastos
    out, _ = _decir(bot, ctx, "gastos en comida")
    assert "comida" in out and "salario" not in out


def test_bot_pregunta_la_categoria_si_falta(cfg):
    bot = hacer_bot(cfg)
    ctx = contexto()
    out, _ = _decir(bot, ctx, "gasté 20 mil")
    assert "¿En qué categoría?" in out and bot.db.movimientos(1) == []  # aún no guardó nada «a ciegas»
    out, _ = _decir(bot, ctx, "transporte")
    assert "Registrado" in out
    (mov,) = bot.db.movimientos(1)
    assert mov["categoria"] == "transporte" and mov["monto"] == 20000 and mov["descripcion"] == "gasté 20 mil"


def test_bot_presupuestos_y_alertas(cfg):
    bot = hacer_bot(cfg)
    ctx = contexto()
    out, _ = _decir(bot, ctx, "presupuesto de comida 100 mil")
    assert "presupuesto de comida" in out and "100.000" in out
    out, _ = _decir(bot, ctx, "gasté 85 mil en comida")
    assert "85 %" in out
    out, _ = _decir(bot, ctx, "ver presupuestos")
    assert "comida" in out and "85 %" in out
    out, _ = _decir(bot, ctx, "quitar presupuesto de comida")
    assert "Quité" in out
    out, _ = _decir(bot, ctx, "💼 Presupuestos")
    assert "Aún no tienes presupuestos" in out


def test_bot_flujo_guiado_de_presupuesto(cfg):
    bot = hacer_bot(cfg)
    ctx = contexto()
    _decir(bot, ctx, "💼 Presupuestos")
    out, _ = _decir(bot, ctx, "presupuesto")
    assert "Aún no tienes" in out or "Presupuestos" in out
    u, m = hacer_update("x")
    asyncio.run(bot._accion("presupuesto", 1, 101, "Luis", ctx.user_data, lambda t, **kw: m.reply_text(t, **kw)))
    assert ctx.user_data["flujo"]["tipo"] == "presupuesto"
    out, _ = _decir(bot, ctx, "ropa")
    assert "tope mensual para ropa" in out
    out, _ = _decir(bot, ctx, "300 mil")
    assert "300.000" in out and bot.servicio.listar_presupuestos(1)["presupuestos"][0]["categoria"] == "ropa"


def test_bot_recordatorios_nuevos_y_charla(cfg):
    bot = hacer_bot(cfg)
    ctx = contexto()
    assert "cada 2 horas" in _decir(bot, ctx, "recuérdame tomar agua cada 2 horas")[0]
    assert "cada semana" in _decir(bot, ctx, "recuérdame ir al gym todos los lunes a las 6 am")[0]
    assert "Hola" in _decir(bot, ctx, "hola")[0] and "De nada" in _decir(bot, ctx, "gracias")[0]
    assert "Presupuestos" in _decir(bot, ctx, "qué puedes hacer")[0]
    assert "mínimo" in _decir(bot, ctx, "recuérdame estirarme cada 2 minutos")[0]
    out, _ = _decir(bot, ctx, "tengo que pagar el arriendo el viernes")
    assert "Tarea 1 creada" in out


def test_bot_hace_respaldo_al_iniciar(cfg, tmp_path):
    bot = hacer_bot(cfg)
    bot._respaldar()
    assert list((tmp_path / "backups").glob("helassist-*.db"))
