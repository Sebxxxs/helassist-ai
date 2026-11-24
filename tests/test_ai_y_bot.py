"""Pruebas del asistente conversacional (con un cliente falso de Anthropic) y del flujo del bot."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

from helassist.ai import HERRAMIENTAS, HERRAMIENTAS_API, Asistente
from helassist.bot import HelAssistBot, LimiteTasa, _partir


def texto(t):
    return NS(type="text", text=t)


def uso(id_, nombre, **args):
    return NS(type="tool_use", id=id_, name=nombre, input=args)


class ClienteFalso:
    """Devuelve respuestas guiadas y guarda las peticiones recibidas."""

    def __init__(self, respuestas):
        self.respuestas, self.peticiones = list(respuestas), []
        self.messages = NS(create=self._create)

    async def _create(self, **kw):
        self.peticiones.append(kw)
        return self.respuestas.pop(0)


def resp(stop, *bloques):
    return NS(stop_reason=stop, content=list(bloques))


def test_esquema_de_herramientas_valido():
    nombres = [h["name"] for h in HERRAMIENTAS]
    assert len(nombres) == len(set(nombres))
    for h in HERRAMIENTAS:
        assert h["input_schema"]["type"] == "object" and h["description"]
        for req in h["input_schema"].get("required", []):
            assert req in h["input_schema"]["properties"]


def test_conversacion_libre_sin_herramientas(servicio, db, cfg):
    cliente = ClienteFalso([resp("end_turn", texto("Un CDT es un depósito a término fijo."))])
    a = Asistente(servicio, db, cfg, cliente)
    out = asyncio.run(a.responder(1, 10, "Luis", "¿Qué es un CDT?"))
    assert "depósito" in out
    assert cliente.peticiones[0]["tools"] == HERRAMIENTAS_API
    sistema = " ".join(b["text"] for b in cliente.peticiones[0]["system"])
    assert "Luis" in sistema and "Hoy es" in sistema
    assert [m["content"] for m in db.ultimos_mensajes(1)] == ["¿Qué es un CDT?", out]


def test_lenguaje_natural_usa_herramienta_y_guarda_en_bd(servicio, db, cfg):
    cliente = ClienteFalso([
        resp("tool_use", texto("Voy a registrarlo."), uso("t1", "registrar_movimiento", tipo="gasto", monto=40000, categoria="cine")),
        resp("end_turn", texto("Listo, anoté 40 mil en cine 🎬")),
    ])
    a = Asistente(servicio, db, cfg, cliente)
    out = asyncio.run(a.responder(1, 10, None, "ayer me gasté como 40 lucas en el cine"))
    assert "40 mil" in out
    assert servicio.consultar_finanzas(1)["gastos"] == 40000
    # el resultado de la herramienta se devolvió a la IA
    ultimo = cliente.peticiones[1]["messages"][-1]
    assert ultimo["role"] == "user" and ultimo["content"][0]["type"] == "tool_result"
    assert ultimo["content"][0]["is_error"] is False


def test_errores_de_herramienta_llegan_a_la_ia_sin_romper(servicio, db, cfg):
    cliente = ClienteFalso([
        resp("tool_use", uso("t1", "registrar_movimiento", tipo="gasto", monto=-5)),
        resp("tool_use", uso("t2", "herramienta_inventada")),
        resp("end_turn", texto("No pude registrar eso.")),
    ])
    a = Asistente(servicio, db, cfg, cliente)
    assert asyncio.run(a.responder(1, 10, None, "x")) == "No pude registrar eso."
    assert cliente.peticiones[1]["messages"][-1]["content"][0]["is_error"] is True
    assert cliente.peticiones[2]["messages"][-1]["content"][0]["is_error"] is True


def test_bucle_infinito_de_herramientas_se_corta(servicio, db, cfg):
    cliente = ClienteFalso([resp("tool_use", uso(f"t{i}", "listar_tareas")) for i in range(10)])
    a = Asistente(servicio, db, cfg, cliente)
    assert "repetir" in asyncio.run(a.responder(1, 10, None, "x"))
    assert len(cliente.peticiones) == 6


def test_recordatorio_por_ia(servicio, db, cfg):
    from datetime import timedelta
    f = (servicio.hoy() + timedelta(days=2)).isoformat()
    cliente = ClienteFalso([
        resp("tool_use", uso("t1", "crear_recordatorio", mensaje="examen", fecha=f, hora="07:00")),
        resp("end_turn", texto("Listo")),
    ])
    asyncio.run(Asistente(servicio, db, cfg, cliente).responder(1, 10, None, "avísame del examen"))
    assert servicio.listar_recordatorios(1)["recordatorios"][0]["mensaje"] == "examen"


def test_sin_api_key_no_hay_asistente(servicio, db, cfg):
    assert not Asistente(servicio, db, cfg).disponible


# ------------------------------------------------------------------ bot
def hacer_bot(cfg, cliente=None, aceptado=True):
    bot = HelAssistBot(cfg)
    if cliente:
        bot.asistente.cliente = cliente
    if aceptado:  # la mayoría de pruebas asume que el usuario ya autorizó el tratamiento de datos
        bot._acepto = lambda uid: True
    return bot


def hacer_update(msg, uid=1):
    m = MagicMock()
    m.text = msg
    m.reply_text = AsyncMock()
    u = NS(
        effective_user=NS(id=uid, first_name="Luis"),
        effective_chat=NS(id=100 + uid),
        effective_message=m,
        message=m,
    )
    return u, m


def contexto():
    return NS(bot=NS(send_chat_action=AsyncMock()), user_data={})


def respuestas(m):
    return [c.args[0] for c in m.reply_text.call_args_list]


def test_bot_se_construye_sin_red(cfg):
    assert hacer_bot(cfg).construir() is not None


def test_bot_reglas_sin_ia(cfg):
    bot = hacer_bot(cfg)
    for entrada, esperado in [
        ("Gasté 20 mil en comida", "Registrado"),
        ("Recibí 1,5 millones de salario", "Registrado"),
        ("Crear tarea estudiar mañana", "Tarea 1 creada"),
        ("Recuérdame pagar Netflix mañana a las 8:30", "te recordaré"),
        ("Resumen", "Total gastos"),
        ("📋 Ver tareas", "ID 1"),
        ("📌 Ver recordatorios", "Netflix"),
        ("Análisis", "Gastas el"),
        ("completar tarea 1", "completada"),
        ("hola", "Soy HelAssist"),
        ("cuéntame un chiste", "No estoy seguro"),
        ("¿Qué es un CDT?", "modo de conversación con IA"),
        ("💸 Gasto", "¿De cuánto fue el gasto?"),
    ]:
        u, m = hacer_update(entrada)
        asyncio.run(bot._procesar(u, contexto(), entrada))
        assert esperado in " ".join(respuestas(m)), (entrada, respuestas(m))


def test_bot_reiniciar_pide_confirmacion(cfg):
    bot = hacer_bot(cfg)
    u, m = hacer_update("Gasté 5 mil en comida")
    asyncio.run(bot._procesar(u, contexto(), "Gasté 5 mil en comida"))
    u, m = hacer_update("♻ Reiniciar")
    asyncio.run(bot._procesar(u, contexto(), "♻ Reiniciar"))
    assert "Seguro" in respuestas(m)[0] or "seguro" in respuestas(m)[0]
    assert bot.servicio.consultar_finanzas(1)["gastos"] == 5000  # aún no se borró nada


def test_bot_pregunta_va_a_la_ia_y_gasto_va_por_reglas(cfg):
    cliente = ClienteFalso([resp("end_turn", texto("Llevas $20.000 en comida."))])
    bot = hacer_bot(cfg, cliente)
    u, m = hacer_update("Gasté 20 mil en comida")
    asyncio.run(bot._procesar(u, contexto(), "Gasté 20 mil en comida"))
    assert cliente.peticiones == []  # las reglas lo resolvieron: cero costo de API
    u, m = hacer_update("¿Cuánto llevo en comida?")
    asyncio.run(bot._procesar(u, contexto(), "¿Cuánto llevo en comida?"))
    assert len(cliente.peticiones) == 1 and "20.000" in respuestas(m)[0]


def test_bot_conversacion_libre_con_ia(cfg):
    cliente = ClienteFalso([resp("end_turn", texto("¡Hola Luis! ¿En qué te ayudo?"))])
    bot = hacer_bot(cfg, cliente)
    u, m = hacer_update("hola, cuéntame algo interesante")
    asyncio.run(bot._procesar(u, contexto(), "hola, cuéntame algo interesante"))
    assert respuestas(m) == ["¡Hola Luis! ¿En qué te ayudo?"]


def test_bot_si_falla_la_api_responde_con_gracia(cfg):
    class Roto:
        messages = NS(create=AsyncMock(side_effect=RuntimeError("sin red")))

    bot = hacer_bot(cfg, Roto())
    u, m = hacer_update("cuéntame un chiste")
    asyncio.run(bot._procesar(u, contexto(), "cuéntame un chiste"))
    assert "no puedo conversar" in respuestas(m)[0]


def test_bot_usuario_no_autorizado(cfg):
    from dataclasses import replace

    bot = hacer_bot(replace(cfg, allowed_users=frozenset({42})))
    u, m = hacer_update("Gasté 20 mil", uid=7)
    asyncio.run(bot.on_text(u, contexto()))
    m.reply_text.assert_not_called()


def test_limite_de_tasa():
    lt = LimiteTasa(2)
    assert lt.permitir(1) and lt.permitir(1) and not lt.permitir(1) and lt.permitir(2)


def test_partir_mensajes_largos():
    partes = _partir("\n".join(["x" * 100] * 100))
    assert all(len(p) <= 4000 for p in partes) and len(partes) > 1


# ---------------------------------------------------- botones y flujos guiados
def hacer_callback(data, uid=1):
    msg = MagicMock()
    msg.chat_id = 100 + uid
    msg.reply_text = AsyncMock()
    q = NS(data=data, from_user=NS(id=uid, first_name="Luis"), message=msg, answer=AsyncMock(),
           edit_message_reply_markup=AsyncMock(), edit_message_text=AsyncMock())
    return NS(callback_query=q, effective_user=NS(id=uid)), msg


def escribir(bot, ctx, texto, uid=1):
    u, m = hacer_update(texto, uid)
    asyncio.run(bot._procesar(u, ctx, texto))
    return respuestas(m)


def tocar(bot, ctx, data, uid=1):
    u, m = hacer_callback(data, uid)
    asyncio.run(bot.on_callback(u, ctx))
    return [c.args[0] for c in m.reply_text.call_args_list]


def test_recordatorio_el_6_de_cada_mes_se_entiende(cfg):
    bot = hacer_bot(cfg)
    r = escribir(bot, contexto(), "recuerdame pagar netflix el 6 de cada mes")
    assert "te recordaré" in r[0] and "cada mes" in r[0]
    rec = bot.servicio.listar_recordatorios(1)["recordatorios"][0]
    assert rec["repeticion"] == "mensual" and rec["fecha_hora"].split("T")[0].endswith("-06")


def test_flujo_gasto_con_botones(cfg):
    bot, ctx = hacer_bot(cfg), contexto()
    assert "¿De cuánto fue el gasto?" in escribir(bot, ctx, "💸 Gasto")[0]
    assert "Monto: $20.000" in escribir(bot, ctx, "20 mil")[0]
    assert "Registrado" in tocar(bot, ctx, "f:comida")[0]
    r = bot.servicio.consultar_finanzas(1)
    assert r["gastos"] == 20000 and r["gastos_por_categoria"] == {"comida": 20000}
    assert "flujo" not in ctx.user_data


def test_flujo_ingreso_categoria_escrita_y_monto_invalido(cfg):
    bot, ctx = hacer_bot(cfg), contexto()
    escribir(bot, ctx, "💰 Ingreso")
    assert "No entendí el monto" in escribir(bot, ctx, "mucho")[0]
    escribir(bot, ctx, "1,5 millones")
    assert "Escribe la categoría" in tocar(bot, ctx, "f:otra")[0]
    escribir(bot, ctx, "mesada")
    assert bot.servicio.consultar_finanzas(1)["ingresos"] == 1500000


def test_flujo_escribir_frase_completa_en_paso_monto_no_se_atasca(cfg):
    bot, ctx = hacer_bot(cfg), contexto()
    escribir(bot, ctx, "💸 Gasto")
    assert "Registrado" in escribir(bot, ctx, "Gasté 5 mil en comida")[0]
    assert "flujo" not in ctx.user_data


def test_flujo_tarea_con_botones(cfg):
    bot, ctx = hacer_bot(cfg), contexto()
    escribir(bot, ctx, "✅ Nueva tarea")
    assert "¿Para cuándo es?" in escribir(bot, ctx, "estudiar cálculo")[0]
    assert "Tarea 1 creada" in tocar(bot, ctx, "f:manana")[0]
    assert bot.servicio.listar_tareas(1)["tareas"][0]["fecha"] is not None


def test_flujo_tarea_con_fecha_incluida_salta_pasos(cfg):
    bot, ctx = hacer_bot(cfg), contexto()
    escribir(bot, ctx, "✅ Nueva tarea")
    assert "Tarea 1 creada" in escribir(bot, ctx, "entregar informe mañana")[0]


def test_flujo_recordatorio_completo_con_botones(cfg):
    bot, ctx = hacer_bot(cfg), contexto()
    escribir(bot, ctx, "⏰ Recordatorio")
    assert "¿Para qué día?" in escribir(bot, ctx, "llamar al banco")[0]
    assert "¿A qué hora?" in tocar(bot, ctx, "f:manana")[0]
    assert "¿Se repite?" in tocar(bot, ctx, "f:09:00")[0]
    assert "te recordaré" in tocar(bot, ctx, "f:semanal")[0]
    rec = bot.servicio.listar_recordatorios(1)["recordatorios"][0]
    assert rec["mensaje"] == "llamar al banco" and rec["fecha_hora"].endswith("T09:00") and rec["repeticion"] == "semanal"


def test_recordatorio_sin_fecha_sigue_con_preguntas(cfg):
    bot, ctx = hacer_bot(cfg), contexto()
    assert "¿Para qué día?" in escribir(bot, ctx, "recuérdame pagar netflix")[0]
    assert ctx.user_data["flujo"]["datos"]["mensaje"] == "pagar netflix"
    assert "¿A qué hora?" in escribir(bot, ctx, "el 6 de cada mes")[0]
    assert "te recordaré" in escribir(bot, ctx, "8 am")[0]
    rec = bot.servicio.listar_recordatorios(1)["recordatorios"][0]
    assert rec["repeticion"] == "mensual" and rec["fecha_hora"].endswith("T08:00")


def test_cancelar_flujo_y_boton_expirado(cfg):
    bot, ctx = hacer_bot(cfg), contexto()
    escribir(bot, ctx, "💸 Gasto")
    assert "Cancelado" in tocar(bot, ctx, "f:cancelar")[0] and "flujo" not in ctx.user_data
    assert "expiró" in tocar(bot, ctx, "f:comida", uid=1)[0]


def test_elegir_otra_opcion_cancela_flujo_en_curso(cfg):
    bot, ctx = hacer_bot(cfg), contexto()
    escribir(bot, ctx, "💸 Gasto")
    escribir(bot, ctx, "📊 Resumen")
    assert "flujo" not in ctx.user_data


def test_menu_inline_ejecuta_acciones(cfg):
    bot, ctx = hacer_bot(cfg), contexto()
    assert "¿De cuánto fue el ingreso?" in tocar(bot, ctx, "menu:ingreso")[0]
    assert "pregunta" in tocar(bot, ctx, "menu:preguntar")[0] or "conversación" in tocar(bot, ctx, "menu:preguntar")[0]
    assert "Ejemplos" in tocar(bot, ctx, "menu:ayuda")[0]
    assert "No tienes tareas" in tocar(bot, ctx, "menu:tareas")[0]


def test_menu_incluye_todas_las_actividades(cfg):
    from helassist.bot import BOTONES_MENU, ETIQUETA_A_ACCION, menu_inline, menu_reply

    acciones = {a for _, a in BOTONES_MENU}
    assert {"gasto", "ingreso", "tarea", "tareas", "recordatorio", "recordatorios", "resumen",
            "analisis", "reporte", "movimientos", "deshacer", "ayuda"} <= acciones
    assert acciones <= set(ETIQUETA_A_ACCION.values())
    assert len(menu_inline(True).inline_keyboard) >= 6 and len(menu_reply(False).keyboard) >= 6
    for fila in menu_inline(True).inline_keyboard:
        for b in fila:
            assert len(b.callback_data.encode()) <= 64


def test_con_ia_las_preguntas_siguen_yendo_a_la_ia_en_vez_de_los_botones(cfg):
    cliente = ClienteFalso([resp("end_turn", texto("Un CDT es un depósito a término fijo."))])
    bot = hacer_bot(cfg, cliente)
    assert "depósito" in escribir(bot, contexto(), "¿Qué es un CDT?")[0]
