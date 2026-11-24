"""Bot de Telegram (python-telegram-bot v21+, asíncrono).

Formas de usarlo (todas funcionan a la vez):
  • Botones: menú fijo abajo + menú con botones en el chat (/menu). Gasto, ingreso, tarea y recordatorio
    se piden paso a paso, con botones para elegir categoría, fecha, hora y repetición.
  • Frases naturales: «Gasté 20 mil en comida», «Recuérdame pagar Netflix el 6 de cada mes».
  • Conversación libre con IA (si hay ANTHROPIC_API_KEY): preguntas generales y consultas sobre tus datos.

Flujo de cada mensaje de texto:
  1. Botón del menú            → acción directa o flujo guiado.
  2. Flujo guiado en curso     → se interpreta la respuesta como el siguiente paso.
  3. ¿Es una pregunta?          → asistente IA (si está configurado).
  4. Frases con estructura      → reglas rápidas (sin costo de API).
  5. Todo lo demás              → asistente IA, o ayuda con botones si no hay IA.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Any, Awaitable, Callable

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import formato, voz
from .ai import Asistente
from .config import Config
from .db import Database
from .parser import (
    buscar_fecha,
    buscar_hora,
    buscar_repeticion,
    detectar_intencion,
    es_pregunta,
    extraer_cantidad,
    fmt_cop,
    mensaje_recordatorio,
    normalizar_categoria,
    parse_hora_simple,
    parse_recordatorio,
    parse_tarea,
    plano,
)
from .recordatorios import Recordatorios
from .servicio import Servicio

log = logging.getLogger("helassist")
Enviar = Callable[..., Awaitable[Any]]
FLUJO_TTL = 600  # segundos que dura un flujo guiado sin respuesta

# ------------------------------------------------------------------ teclados
BOTONES_MENU = [
    ("💸 Gasto", "gasto"), ("💰 Ingreso", "ingreso"),
    ("✅ Nueva tarea", "tarea"), ("📋 Ver tareas", "tareas"),
    ("⏰ Recordatorio", "recordatorio"), ("📌 Ver recordatorios", "recordatorios"),
    ("📊 Resumen", "resumen"), ("📝 Análisis", "analisis"),
    ("📆 Reporte mensual", "reporte"), ("🧾 Movimientos", "movimientos"),
    ("💼 Presupuestos", "presupuestos"), ("↩️ Deshacer", "deshacer"), ("❓ Ayuda", "ayuda"),
]
# Etiquetas escritas por el usuario (sin emojis ni tildes) → acción
ETIQUETA_A_ACCION = {
    "gasto": "gasto", "ingreso": "ingreso", "nueva tarea": "tarea", "ver tareas": "tareas",
    "recordatorio": "recordatorio", "ver recordatorios": "recordatorios", "resumen": "resumen",
    "analisis": "analisis", "reporte mensual": "reporte", "movimientos": "movimientos",
    "deshacer": "deshacer", "ayuda": "ayuda", "presupuestos": "presupuestos", "presupuesto": "presupuestos", "reiniciar": "reiniciar", "preguntar": "preguntar",
    "menu": "menu",
}
CATS_GASTO = [("🍔 Comida", "comida"), ("🚌 Transporte", "transporte"), ("🏠 Vivienda", "vivienda"),
              ("💡 Servicios", "servicios"), ("🎬 Ocio", "entretenimiento"), ("🩺 Salud", "salud"),
              ("📚 Educación", "educacion"), ("👕 Ropa", "ropa")]
CATS_INGRESO = [("💼 Salario", "salario"), ("🛒 Ventas", "ventas"),
                ("💻 Freelance", "freelance"), ("🎁 Regalo", "regalo")]
HORAS_RAPIDAS = ["07:00", "09:00", "12:00", "15:00", "18:00", "20:00"]

AVISO_PRIVACIDAD = (
    "🔐 Antes de empezar: autorización para tratar tus datos\n\n"
    "Para funcionar, HelAssist AI guarda tu ID de Telegram, tu nombre de pila y lo que registres "
    "(gastos, ingresos, tareas, recordatorios y presupuestos). Los usamos solo para prestarte el servicio "
    "y no los vendemos ni los compartimos con fines comerciales.\n"
    "{ia}"
    "Puedes consultar tus datos cuando quieras y borrarlos todos con «♻ Reiniciar».\n\n"
    "¿Autorizas el tratamiento de tus datos en estas condiciones (Ley 1581 de 2012)?"
)
AVISO_IA = (
    "Cuando conversas con la IA, el texto de tus mensajes y los datos que ella consulta se envían a "
    "Anthropic (Estados Unidos), el proveedor del modelo, para generar la respuesta. Las notas de voz "
    "se transcriben en nuestro servidor.\n"
)
SIN_AUTORIZACION = ("Entendido. Sin tu autorización no puedo guardar tus datos, así que no te puedo ayudar "
                    "todavía. Si cambias de opinión, escribe /start.")


def _kb(filas: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in f] for f in filas])


CANCELAR = [("❌ Cancelar", "f:cancelar")]


def menu_reply(ia: bool) -> ReplyKeyboardMarkup:
    filas = [[b[0] for b in BOTONES_MENU[i:i + 2]] for i in range(0, len(BOTONES_MENU), 2)]
    filas.append(["💬 Preguntar", "♻ Reiniciar"] if ia else ["♻ Reiniciar"])
    return ReplyKeyboardMarkup(filas, resize_keyboard=True)


def menu_inline(ia: bool) -> InlineKeyboardMarkup:
    filas = [[(t, f"menu:{a}") for t, a in BOTONES_MENU[i:i + 2]] for i in range(0, len(BOTONES_MENU), 2)]
    filas.append([("💬 Preguntar", "menu:preguntar"), ("♻ Reiniciar", "menu:reiniciar")] if ia
                 else [("♻ Reiniciar", "menu:reiniciar")])
    return _kb(filas)


def kb_privacidad() -> InlineKeyboardMarkup:
    return _kb([[("✅ Acepto", "priv:si"), ("❌ No acepto", "priv:no")]])


def kb_cancelar() -> InlineKeyboardMarkup:
    return _kb([CANCELAR])


def kb_categorias(tipo: str) -> InlineKeyboardMarkup:
    cats = CATS_GASTO if tipo == "gasto" else CATS_INGRESO
    filas = [[(l, f"f:{s}") for l, s in cats[i:i + 2]] for i in range(0, len(cats), 2)]
    return _kb(filas + [[("✏️ Otra categoría", "f:otra")], CANCELAR])


def kb_fecha(con_sin_fecha: bool) -> InlineKeyboardMarkup:
    filas = [[("📅 Hoy", "f:hoy"), ("📅 Mañana", "f:manana")]]
    if con_sin_fecha:
        filas.append([("Sin fecha", "f:ninguna")])
    return _kb(filas + [CANCELAR])


def kb_hora() -> InlineKeyboardMarkup:
    filas = [[(f"🕐 {h.lstrip('0')}", f"f:{h}") for h in HORAS_RAPIDAS[i:i + 3]] for i in range(0, 6, 3)]
    return _kb(filas + [CANCELAR])


def kb_repeticion() -> InlineKeyboardMarkup:
    return _kb([[("Una sola vez", "f:ninguna")],
                [("Cada día", "f:diario"), ("Cada semana", "f:semanal"), ("Cada mes", "f:mensual")],
                CANCELAR])


# -------------------------------------------------------------------- textos
BIENVENIDA = (
    "¡Hola{nombre}! Soy HelAssist AI 🤖\n\n"
    "Te ayudo con tus finanzas, tareas y recordatorios. Puedes usar los botones de abajo o escribirme "
    "(o mandarme una nota de voz) como le hablarías a un amigo:\n"
    "• «Gasté 20 mil en comida»\n"
    "• «Me llegó la quincena, 700 mil»\n"
    "• «Recuérdame pagar Netflix el 6 de cada mes»\n"
    "• «¿Cuánto llevo gastado en transporte este mes?»\n"
    "• «Gasté 20 mil en comida y 15 mil en transporte» (varios a la vez)\n"
    "{extra}\n"
    "Escribe /menu para ver el menú con botones y /ayuda para ver más ejemplos."
)
EXTRA_IA = "• O pregúntame lo que quieras: ahorro, estudio, ideas, dudas del día a día 💬\n"

AYUDA = (
    "📖 Ejemplos de lo que entiendo\n\n"
    "💸 Gastos e ingresos\n"
    "«Gasté 20 mil en comida», «Pagué 100.000 de arriendo», «Recibí 1,5 millones de salario»\n"
    "«Ayer gasté 10 mil en bus y 5 mil en dulces», «Netflix 30000», «Salario 1500000»\n"
    "↩️ /deshacer borra el último movimiento\n\n"
    "✅ Tareas\n"
    "«Crear tarea estudiar cálculo mañana», «Tengo que pagar el arriendo el viernes», «completar tarea 2»\n\n"
    "⏰ Recordatorios y alarmas\n"
    "«Recuérdame llamar a mamá el viernes a las 6 pm», «Pon una alarma a las 6 am»\n"
    "«Recuérdame tomar agua en 30 minutos», «…cada 2 horas», «…todos los lunes a las 7 am»\n"
    "«Recuérdame pagar Netflix el 6 de cada mes»\n\n"
    "💼 Presupuestos\n"
    "«Presupuesto de comida 500 mil»: te aviso al llegar al 80 % y al 100 %. «Ver presupuestos»\n\n"
    "📊 Consultas y reportes\n"
    "«¿Cuánto gasté en comida este mes?», «Ver mis ingresos», «Resumen de la semana», «Movimientos de ayer»\n"
    "«Análisis», «Reporte mensual»\n\n"
    "🎤 También entiendo notas de voz.\n"
    "👆 Si prefieres botones, toca cualquiera del menú y te voy guiando paso a paso.{ia}"
)
AYUDA_IA = "\n💬 Y puedes conversar conmigo de cualquier tema o hacerme preguntas sobre tus finanzas."

SIN_IA = (
    "No estoy seguro de qué quieres hacer con eso 🤔\n"
    "Puedes elegir una opción del menú o escribir algo como «Gasté 20 mil en comida»."
)
SIN_IA_PREGUNTA = (
    "Esa es una pregunta general 💬 y para responderla necesito mi modo de conversación con IA, "
    "que todavía no está activado en este bot.\n\n"
    "Mientras tanto puedo ayudarte con tus finanzas, tareas y recordatorios. Elige una opción:"
)
PREGUNTAR_CON_IA = "💬 ¡Claro! Escríbeme tu pregunta o cuéntame lo que necesites, como si hablaras con un amigo."
PREGUNTAR_SIN_IA = ("💬 La conversación libre todavía no está activada en este bot. "
                    "Mientras tanto puedo ayudarte con las opciones del menú:")


class LimiteTasa:
    """Máximo N mensajes por minuto por usuario hacia la IA (protege tu factura de la API)."""

    def __init__(self, maximo: int):
        self.maximo, self.eventos = maximo, defaultdict(deque)

    def permitir(self, user_id: int) -> bool:
        ahora, cola = time.monotonic(), self.eventos[user_id]
        while cola and ahora - cola[0] > 60:
            cola.popleft()
        if len(cola) >= self.maximo:
            return False
        cola.append(ahora)
        return True


def _partir(texto: str, limite: int = 4000) -> list[str]:
    partes, actual = [], ""
    for linea in texto.split("\n"):
        while len(linea) > limite:
            partes.append(linea[:limite])
            linea = linea[limite:]
        if len(actual) + len(linea) + 1 > limite:
            partes.append(actual)
            actual = ""
        actual += linea + "\n"
    if actual.strip():
        partes.append(actual.rstrip("\n"))
    return partes or [texto]


async def _enviar_msg(mensaje, texto: str, **kw) -> None:
    partes = _partir(texto)
    for i, parte in enumerate(partes):
        await mensaje.reply_text(parte, **(kw if i == len(partes) - 1 else {}))


def configurar_logs() -> None:
    os.makedirs("logs", exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    archivo = RotatingFileHandler("logs/helassist.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    consola = logging.StreamHandler()
    for h in (archivo, consola):
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[archivo, consola])
    # httpx escribe en INFO la URL de cada petición, y esa URL incluye el token del bot.
    for ruidoso in ("httpx", "httpcore", "apscheduler"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


class HelAssistBot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = Database(cfg.db_path)
        self.recordatorios = Recordatorios(self.db, cfg.tz, self._enviar_a_chat)
        self.servicio = Servicio(self.db, cfg, self.recordatorios)
        self.asistente = Asistente(self.servicio, self.db, cfg)
        self.limite = LimiteTasa(cfg.llm_max_por_minuto)
        self.app: Application | None = None

    # ---------------------------------------------------------------- ciclo
    def construir(self) -> Application:
        app = (
            Application.builder()
            .token(self.cfg.telegram_token)
            .connect_timeout(20).read_timeout(20).write_timeout(20).pool_timeout(20)
            .get_updates_connect_timeout(20).get_updates_read_timeout(30)
            .post_init(self._post_init)
            .post_shutdown(self._post_shutdown)
            .build()
        )
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("menu", self.cmd_menu))
        app.add_handler(CommandHandler(["ayuda", "help"], self.cmd_ayuda))
        app.add_handler(CommandHandler("cancelar", self.cmd_cancelar))
        for nombre, accion in [("deshacer", "deshacer"), ("resumen", "resumen"), ("analisis", "analisis"),
                               ("reporte", "reporte"), ("tareas", "tareas"), ("recordatorios", "recordatorios"),
                               ("gasto", "gasto"), ("ingreso", "ingreso"), ("tarea", "tarea"),
                               ("recordatorio", "recordatorio"), ("presupuestos", "presupuestos")]:
            app.add_handler(CommandHandler(nombre, self.cmd_accion(accion)))
        app.add_handler(CallbackQueryHandler(self.on_callback))
        app.add_handler(MessageHandler(filters.VOICE, self.on_voice))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        app.add_error_handler(self.on_error)
        self.app = app
        return app

    def _respaldar(self) -> None:
        try:
            destino = self.db.respaldar(self.cfg.carpeta_respaldos, self.cfg.backups_conservar)
            log.info("Respaldo de la base de datos: %s", destino)
        except Exception:
            log.exception("No se pudo respaldar la base de datos")

    async def _post_init(self, app: Application) -> None:
        self.recordatorios.iniciar()
        self._respaldar()  # al arrancar y, después, todos los días a las 3:00
        self.recordatorios.scheduler.add_job(self._respaldar, "cron", hour=3, minute=0,
                                             id="respaldo-bd", replace_existing=True)
        await app.bot.set_my_commands([
            ("menu", "Menú con botones"),
            ("gasto", "Registrar un gasto"),
            ("ingreso", "Registrar un ingreso"),
            ("tarea", "Crear una tarea"),
            ("recordatorio", "Crear un recordatorio"),
            ("resumen", "Resumen financiero del mes"),
            ("analisis", "Análisis de tus finanzas"),
            ("reporte", "Reporte mensual"),
            ("tareas", "Ver tareas pendientes"),
            ("recordatorios", "Ver recordatorios"),
            ("presupuestos", "Presupuestos por categoría"),
            ("deshacer", "Borrar el último movimiento"),
            ("ayuda", "Ejemplos de lo que puedo hacer"),
        ])
        log.info("HelAssist AI listo. Asistente IA: %s", "activado" if self.asistente.disponible else "desactivado")

    async def _post_shutdown(self, app: Application) -> None:
        self.recordatorios.detener()

    async def _enviar_a_chat(self, chat_id: int, texto: str) -> None:
        assert self.app is not None
        await self.app.bot.send_message(chat_id=chat_id, text=texto)

    # ------------------------------------------------------------- utilidades
    def _autorizado(self, user_id: int) -> bool:
        return not self.cfg.allowed_users or user_id in self.cfg.allowed_users

    def _acepto(self, user_id: int) -> bool:
        return not self.cfg.exigir_aviso_privacidad or self.db.acepto_privacidad(user_id)

    async def _pedir_privacidad(self, enviar: Enviar) -> None:
        ia = AVISO_IA if self.asistente.disponible else ""
        await enviar(AVISO_PRIVACIDAD.format(ia=ia), reply_markup=kb_privacidad())

    async def _bienvenida(self, enviar: Enviar, nombre: str | None) -> None:
        ia = self.asistente.disponible
        texto = BIENVENIDA.format(nombre=f", {nombre}" if nombre else "", extra=EXTRA_IA if ia else "")
        await enviar(texto, reply_markup=menu_reply(ia))
        await enviar("👇 ¿Qué quieres hacer?", reply_markup=menu_inline(ia))

    async def _responder(self, update: Update, texto: str, **kw) -> None:
        await _enviar_msg(update.effective_message, texto, **kw)

    def _registrar_usuario(self, update: Update) -> tuple[int, int, str | None]:
        u, chat = update.effective_user, update.effective_chat
        self.db.upsert_usuario(u.id, u.first_name, chat.id)
        return u.id, chat.id, u.first_name

    # --------------------------------------------------------------- comandos
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizado(update.effective_user.id):
            return await self._responder(update, "Lo siento, este bot es privado. 🔒")
        uid, _, nombre = self._registrar_usuario(update)
        context.user_data.pop("flujo", None)
        enviar: Enviar = lambda t, **kw: self._responder(update, t, **kw)  # noqa: E731
        if not self._acepto(uid):
            return await self._pedir_privacidad(enviar)
        await self._bienvenida(enviar, nombre)

    async def cmd_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizado(update.effective_user.id):
            return
        uid, _, _ = self._registrar_usuario(update)
        if not self._acepto(uid):
            return await self._pedir_privacidad(lambda t, **kw: self._responder(update, t, **kw))
        ia = self.asistente.disponible
        await self._responder(update, "👇 ¿Qué quieres hacer?", reply_markup=menu_inline(ia))

    async def cmd_ayuda(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizado(update.effective_user.id):
            return
        ia = self.asistente.disponible
        await self._responder(update, AYUDA.format(ia=AYUDA_IA if ia else ""), reply_markup=menu_inline(ia))

    async def cmd_cancelar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizado(update.effective_user.id):
            return
        hubo = context.user_data.pop("flujo", None)
        await self._responder(update, "Cancelado 👍" if hubo else "No hay nada que cancelar.")

    def cmd_accion(self, accion: str):
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not self._autorizado(update.effective_user.id):
                return
            uid, cid, nombre = self._registrar_usuario(update)
            enviar: Enviar = lambda t, **kw: self._responder(update, t, **kw)  # noqa: E731
            if not self._acepto(uid):
                return await self._pedir_privacidad(enviar)
            await self._accion(accion, uid, cid, nombre, context.user_data, enviar)

        return handler

    # ------------------------------------------------------- acciones del menú
    async def _accion(self, accion: str, uid: int, chat_id: int, nombre: str | None,
                      ud: dict, enviar: Enviar) -> None:
        """Ejecuta una opción del menú (la usan los botones, los comandos y los botones en línea)."""
        ud.pop("flujo", None)  # elegir otra opción cancela cualquier flujo en curso
        ia = self.asistente.disponible
        if accion in ("gasto", "ingreso"):
            ud["flujo"] = {"tipo": accion, "paso": "monto", "datos": {}, "t": time.monotonic()}
            icono = "💸 gasto" if accion == "gasto" else "💰 ingreso"
            return await enviar(f"{icono.split()[0]} ¿De cuánto fue el {accion}? Escribe el monto "
                                "(ej. 20000 o «20 mil»).", reply_markup=kb_cancelar())
        if accion == "tarea":
            ud["flujo"] = {"tipo": "tarea", "paso": "desc", "datos": {}, "t": time.monotonic()}
            return await enviar("✅ ¿Qué tarea quieres crear?\nPuedes incluir la fecha, por ejemplo: "
                                "«estudiar cálculo mañana».", reply_markup=kb_cancelar())
        if accion == "recordatorio":
            ud["flujo"] = {"tipo": "recordatorio", "paso": "mensaje", "datos": {}, "t": time.monotonic()}
            return await enviar("⏰ ¿Qué quieres que te recuerde?\nPuedes escribirlo todo junto, por ejemplo: "
                                "«pagar Netflix el 6 de cada mes».", reply_markup=kb_cancelar())
        if accion == "presupuestos":
            texto, kb = self._vista_presupuestos(uid)
            return await enviar(texto, reply_markup=kb)
        if accion == "presupuesto":
            ud["flujo"] = {"tipo": "presupuesto", "paso": "categoria", "datos": {}, "t": time.monotonic()}
            return await enviar("💼 ¿Para qué categoría quieres el presupuesto mensual?",
                                reply_markup=kb_categorias("gasto"))
        if accion == "ayuda":
            return await enviar(AYUDA.format(ia=AYUDA_IA if ia else ""), reply_markup=menu_inline(ia))
        if accion == "menu":
            return await enviar("👇 ¿Qué quieres hacer?", reply_markup=menu_inline(ia))
        if accion == "preguntar":
            return await enviar(PREGUNTAR_CON_IA if ia else PREGUNTAR_SIN_IA,
                                **({} if ia else {"reply_markup": menu_inline(ia)}))
        intent = {"tareas": "ver_tareas", "recordatorios": "ver_recordatorios", "movimientos": "ver_movimientos"}.get(
            accion, accion)
        texto, kb = self._ejecutar({"intent": intent}, uid, chat_id)
        await enviar(texto, reply_markup=kb)

    def _vista_presupuestos(self, uid: int) -> tuple[str, InlineKeyboardMarkup]:
        r = self.servicio.listar_presupuestos(uid)
        filas = [[("➕ Nuevo presupuesto", "menu:presupuesto")]]
        quitar = [(f"🗑 {p['categoria']}", f"p:{p['categoria']}") for p in r["presupuestos"][:8]]
        filas += [quitar[i:i + 2] for i in range(0, len(quitar), 2)]
        return formato.presupuestos(r), _kb(filas)

    # ------------------------------------------------------ flujos guiados
    def _empezar_flujo(self, ud: dict, tipo: str, paso: str, datos: dict | None = None) -> None:
        ud["flujo"] = {"tipo": tipo, "paso": paso, "datos": datos or {}, "t": time.monotonic()}

    async def _flujo(self, ud: dict, entrada: str, boton: bool, uid: int, chat_id: int, enviar: Enviar) -> None:
        """Avanza un flujo guiado con la respuesta del usuario (texto escrito o botón)."""
        fl = ud["flujo"]
        fl["t"] = time.monotonic()
        s, tipo, paso, d = self.servicio, fl["tipo"], fl["paso"], fl["datos"]
        ahora = s.ahora()
        hoy = ahora.date()

        if boton and entrada == "cancelar":
            ud.pop("flujo", None)
            return await enviar("Cancelado 👍")

        # ---- gasto / ingreso ------------------------------------------------
        if tipo in ("gasto", "ingreso"):
            if paso == "monto":
                monto = extraer_cantidad(entrada)
                if not monto:
                    return await enviar("No entendí el monto 🤔 Escríbelo así: 20000 o «20 mil».",
                                        reply_markup=kb_cancelar())
                d["monto"] = monto
                fl["paso"] = "categoria"
                return await enviar(f"Monto: {fmt_cop(monto)}. ¿En qué categoría?", reply_markup=kb_categorias(tipo))
            if paso == "categoria" and boton and entrada == "otra":
                return await enviar("✏️ Escribe la categoría (ej. mascotas, regalos, salidas):",
                                    reply_markup=kb_cancelar())
            if paso == "categoria":
                ud.pop("flujo", None)
                r = s.registrar_movimiento(uid, tipo, d["monto"], entrada, d.get("desc"), d.get("fecha"))
                return await enviar(r["error"] if "error" in r else formato.movimiento_registrado(r))

        # ---- presupuesto ------------------------------------------------------
        if tipo == "presupuesto":
            if paso == "categoria":
                if boton and entrada == "otra":
                    return await enviar("✏️ Escribe la categoría (ej. mascotas, salidas):", reply_markup=kb_cancelar())
                d["cat"] = normalizar_categoria(entrada)
                fl["paso"] = "monto"
                return await enviar(f"💼 ¿Cuánto es el tope mensual para {d['cat']}? (ej. 300 mil)",
                                    reply_markup=kb_cancelar())
            if paso == "monto":
                monto = extraer_cantidad(entrada)
                if not monto:
                    return await enviar("No entendí el monto 🤔 Escríbelo así: 300000 o «300 mil».",
                                        reply_markup=kb_cancelar())
                ud.pop("flujo", None)
                r = s.definir_presupuesto(uid, d["cat"], monto)
                return await enviar(self._texto_presupuesto(r))

        # ---- tarea ------------------------------------------------------------
        if tipo == "tarea":
            if paso == "desc":
                if len(entrada.strip()) < 2:
                    return await enviar("Escribe la tarea, por favor.", reply_markup=kb_cancelar())
                t = parse_tarea(entrada, hoy)
                if t and t["fecha"]:  # ya trae la fecha: «estudiar mañana»
                    ud.pop("flujo", None)
                    return await enviar(self._texto_tarea(s.crear_tarea(uid, t["descripcion"], t["fecha"])))
                d["desc"] = (t or {"descripcion": entrada.strip()})["descripcion"]
                fl["paso"] = "fecha"
                return await enviar("📅 ¿Para cuándo es? Elige una opción o escribe una fecha "
                                    "(ej. «viernes», «10/03», «en 3 días»).", reply_markup=kb_fecha(True))
            if paso == "fecha":
                if boton and entrada == "ninguna":
                    fecha = None
                else:
                    fecha = hoy if boton and entrada == "hoy" else hoy + timedelta(days=1) if boton and entrada == "manana" \
                        else buscar_fecha(plano(entrada), hoy, futuro=True)[0]
                    if fecha is None:
                        return await enviar("No entendí la fecha 🤔 Prueba con «viernes», «10/03» o «en 3 días».",
                                            reply_markup=kb_fecha(True))
                ud.pop("flujo", None)
                return await enviar(self._texto_tarea(s.crear_tarea(uid, d["desc"], fecha.isoformat() if fecha else None)))

        # ---- recordatorio -----------------------------------------------------
        if tipo == "recordatorio":
            if paso == "mensaje":
                r = parse_recordatorio(entrada, ahora, s.hora_defecto())
                if r and "error" not in r:  # trae todo: «pagar Netflix el 6 de cada mes»
                    ud.pop("flujo", None)
                    return await enviar(self._texto_recordatorio(
                        s.crear_recordatorio(uid, chat_id, r["mensaje"], r["fecha_hora"], r["repeticion"])))
                d["mensaje"] = mensaje_recordatorio(entrada) or entrada.strip()
                fl["paso"] = "fecha"
                return await enviar("📅 ¿Para qué día? Elige una opción o escribe una fecha "
                                    "(ej. «viernes», «10/03», «el 6 de cada mes»).", reply_markup=kb_fecha(False))
            if paso == "fecha":
                if boton:
                    fecha, hora, rep = (hoy if entrada == "hoy" else hoy + timedelta(days=1)), None, None
                else:
                    p = plano(entrada)
                    fecha, hora, rep = buscar_fecha(p, hoy, futuro=True)[0], buscar_hora(p)[0], buscar_repeticion(p)[0]
                if fecha is None:
                    return await enviar("No entendí el día 🤔 Prueba con «mañana», «viernes» o «10/03».",
                                        reply_markup=kb_fecha(False))
                d["fecha"] = fecha
                if rep:
                    d["rep"] = rep
                if hora:
                    d["hora"] = hora
                    return await self._pedir_o_crear_recordatorio(ud, uid, chat_id, enviar, ya_hora=True)
                fl["paso"] = "hora"
                return await enviar("🕐 ¿A qué hora? Elige una o escríbela (ej. 8:30 am, 3 pm, 20:00).",
                                    reply_markup=kb_hora())
            if paso == "hora":
                hora = parse_hora_simple(entrada)
                if hora is None:
                    return await enviar("No entendí la hora 🤔 Escríbela así: 8:30 am, 3 pm o 20:00.",
                                        reply_markup=kb_hora())
                d["hora"] = hora
                return await self._pedir_o_crear_recordatorio(ud, uid, chat_id, enviar, ya_hora=True)
            if paso == "rep":
                if boton:
                    rep = None if entrada == "ninguna" else entrada
                else:
                    rep = buscar_repeticion(plano(entrada))[0]
                    if rep is None and plano(entrada).strip() not in ("no", "ninguna", "una vez", "no se repite", "solo una vez"):
                        return await enviar("Elige cada cuánto se repite con los botones 👇", reply_markup=kb_repeticion())
                d["rep"] = rep
                return await self._pedir_o_crear_recordatorio(ud, uid, chat_id, enviar, ya_hora=True, ya_rep=True)

    async def _pedir_o_crear_recordatorio(self, ud: dict, uid: int, chat_id: int, enviar: Enviar,
                                          ya_hora: bool, ya_rep: bool = False) -> None:
        fl = ud["flujo"]
        d = fl["datos"]
        dt = datetime.combine(d["fecha"], d["hora"])
        if dt <= self.servicio.ahora():
            fl["paso"] = "hora"
            d.pop("hora", None)
            return await enviar("Esa hora ya pasó 😅 Elige otra hora (o cancela y empieza con otro día).",
                                reply_markup=kb_hora())
        if not ya_rep and "rep" not in d:
            fl["paso"] = "rep"
            return await enviar("🔁 ¿Se repite?", reply_markup=kb_repeticion())
        ud.pop("flujo", None)
        r = self.servicio.crear_recordatorio(uid, chat_id, d["mensaje"], dt, d.get("rep"))
        await enviar(self._texto_recordatorio(r))

    @staticmethod
    def _texto_presupuesto(r: dict) -> str:
        if "error" in r:
            return r["error"]
        return (f"💼 Listo: presupuesto de {r['categoria']} = {fmt_cop(r['limite'])} al mes. "
                f"Este mes llevas {fmt_cop(r['gastado_este_mes'])}. Te aviso al llegar al 80 % y al 100 %.")

    @staticmethod
    def _texto_tarea(r: dict) -> str:
        if "error" in r:
            return r["error"]
        vence = f" para el {r['fecha']}" if r["fecha"] else ""
        return f"✅ Tarea {r['num']} creada: {r['descripcion']}{vence}."

    @staticmethod
    def _texto_recordatorio(r: dict) -> str:
        return r["error"] if "error" in r else formato.recordatorio_creado(r)

    # --------------------------------------------------------- ejecutar reglas
    def _ejecutar(self, d: dict, uid: int, chat_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
        """Ejecuta una intención detectada por reglas. Devuelve (texto, teclado_inline|None)."""
        s, intent = self.servicio, d["intent"]
        if intent == "movimiento":
            rs = [s.registrar_movimiento(uid, i["tipo"], i["monto"], i["categoria"], i["descripcion"], i["fecha"])
                  for i in (d.get("items") or [d])]
            ok = [r for r in rs if "error" not in r]
            texto = formato.movimientos_registrados(ok) if ok else ""
            errores = "\n".join(r["error"] for r in rs if "error" in r)
            return "\n".join(x for x in (texto, errores) if x), None
        if intent == "consulta":
            r = s.consultar_finanzas(uid, d.get("desde"), d.get("hasta"), d.get("tipo"), d.get("categoria"))
            return (r["error"] if "error" in r else formato.consulta(r, d)), None
        if intent == "ver_presupuestos":
            return self._vista_presupuestos(uid)
        if intent == "presupuesto":
            return self._texto_presupuesto(s.definir_presupuesto(uid, d["categoria"], d["monto"])), None
        if intent == "borrar_presupuesto":
            r = s.borrar_presupuesto(uid, d["categoria"])
            return (r["error"] if "error" in r else f"🗑 Quité el presupuesto de {r['categoria']}."), None
        if intent == "recordatorio_error":
            return d["error"], None
        if intent == "saludo":
            return ("¡Hola! 👋 Soy HelAssist AI. ¿Qué hacemos hoy? Puedes escribirme normal "
                    "(«gasté 20 mil en comida») o usar los botones:"), menu_inline(self.asistente.disponible)
        if intent == "gracias":
            return "¡De nada! 😊 Aquí estoy para lo que necesites.", None
        if intent == "despedida":
            return "¡Hasta pronto! 👋 Cuando me necesites, escribe /menu.", None
        if intent == "ayuda":
            ia = self.asistente.disponible
            return AYUDA.format(ia=AYUDA_IA if ia else ""), menu_inline(ia)
        if intent == "deshacer":
            r = s.deshacer(uid)
            if "error" in r:
                return r["error"], None
            e = r["eliminado"]
            return f"↩️ Borré el último movimiento: {e['tipo']} de {fmt_cop(e['monto'])} en {e['categoria']}.", None
        if intent == "resumen":
            if d.get("desde"):
                r = s.resumen_periodo(uid, d["desde"], d["hasta"])
                return (r["error"] if "error" in r else formato.resumen(r, d.get("periodo"))), None
            return formato.resumen(s.resumen_mes(uid)), None
        if intent == "analisis":
            return formato.analisis(s.analisis(uid)), None
        if intent == "reporte":
            return formato.reporte(s.reporte_mensual(uid)), None
        if intent == "ver_movimientos":
            return formato.movimientos(s.consultar_finanzas(uid)), None
        if intent == "tarea":
            return self._texto_tarea(s.crear_tarea(uid, d["descripcion"], d["fecha"])), None
        if intent == "ver_tareas":
            r = s.listar_tareas(uid)
            kb = None
            if r["tareas"]:
                botones = [(f"✅ {t['num']}", f"t:{t['num']}") for t in r["tareas"][:12]]
                kb = _kb([botones[i:i + 4] for i in range(0, len(botones), 4)])
            return formato.tareas(r), kb
        if intent == "completar_tarea":
            r = s.completar_tarea(uid, d["num"])
            return (r["error"] if "error" in r else f"🎉 ¡Tarea {r['num']} completada: {r['descripcion']}!"), None
        if intent == "recordatorio":
            r = s.crear_recordatorio(uid, chat_id, d["mensaje"], d["fecha_hora"], d["repeticion"])
            return self._texto_recordatorio(r), None
        if intent == "ver_recordatorios":
            r = s.listar_recordatorios(uid)
            kb = None
            if r["recordatorios"]:
                botones = [(f"🗑 {x['num']}", f"r:{x['num']}") for x in r["recordatorios"][:12]]
                kb = _kb([botones[i:i + 4] for i in range(0, len(botones), 4)])
            return formato.recordatorios(r), kb
        if intent == "borrar_recordatorio":
            r = s.borrar_recordatorio(uid, d["num"])
            return (r["error"] if "error" in r else f"🗑 Recordatorio {r['num']} eliminado: {r['mensaje']}"), None
        if intent == "reiniciar":
            kb = _kb([[("Sí, borrar todo", "reset:si"), ("Cancelar", "reset:no")]])
            return ("⚠️ Esto borrará TODOS tus movimientos, tareas y recordatorios y no se puede deshacer. "
                    "¿Seguro?"), kb
        return SIN_IA, None

    # ------------------------------------------------------------------ texto
    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizado(update.effective_user.id):
            return
        if not self._acepto(update.effective_user.id):
            self._registrar_usuario(update)
            return await self._pedir_privacidad(lambda t, **kw: self._responder(update, t, **kw))
        await self._procesar(update, context, update.message.text or "")

    async def _procesar(self, update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str) -> None:
        texto = texto.strip()
        if not texto:
            return
        uid, chat_id, nombre = self._registrar_usuario(update)
        ud = context.user_data
        enviar: Enviar = lambda t, **kw: self._responder(update, t, **kw)  # noqa: E731

        # 1. Botones del menú escritos/tocados en el teclado fijo
        accion = ETIQUETA_A_ACCION.get(re.sub(r"[^a-z ]", "", plano(texto)).strip())
        if accion:
            return await self._accion(accion, uid, chat_id, nombre, ud, enviar)

        # 2. Flujo guiado en curso
        fl = ud.get("flujo")
        if fl and time.monotonic() - fl["t"] > FLUJO_TTL:
            ud.pop("flujo", None)
            fl = None
        if fl:
            if fl["paso"] == "monto":  # si en vez del monto escribió una frase completa, la procesamos normal
                d0 = detectar_intencion(texto, self.servicio.ahora(), self.servicio.hora_defecto())
                if d0 and d0["intent"] == "movimiento":
                    ud.pop("flujo", None)
                    fl = None
            if fl:
                return await self._flujo(ud, texto, False, uid, chat_id, enviar)

        usar_ia = self.asistente.disponible
        # 3-4. Reglas rápidas (salvo que sea una pregunta y haya IA)
        if not (usar_ia and es_pregunta(texto)):
            d = detectar_intencion(texto, self.servicio.ahora(), self.servicio.hora_defecto())
            if d:
                # Falta el día/hora: seguimos con preguntas guiadas en vez de rendirnos
                if d["intent"] in ("recordatorio_incompleto", "recordatorio_pasado"):
                    msg = d.get("mensaje") or ""
                    if msg:
                        self._empezar_flujo(ud, "recordatorio", "fecha", {"mensaje": msg})
                        pre = "Esa fecha ya pasó." if d["intent"] == "recordatorio_pasado" else "Anotado."
                        return await enviar(f"⏰ {pre} Te recordaré «{msg}». 📅 ¿Para qué día?",
                                            reply_markup=kb_fecha(False))
                    return await self._accion("recordatorio", uid, chat_id, nombre, ud, enviar)
                if d["intent"] == "tarea_incompleta":
                    return await self._accion("tarea", uid, chat_id, nombre, ud, enviar)
                if d["intent"] == "presupuesto_incompleto":
                    return await self._accion("presupuesto", uid, chat_id, nombre, ud, enviar)
                # Gasto/ingreso sin categoría: la preguntamos con botones en vez de guardar «otros» a ciegas
                if d["intent"] == "movimiento" and len(d["items"]) == 1 and d["sin_categoria"]:
                    self._empezar_flujo(ud, d["tipo"], "categoria",
                                        {"monto": d["monto"], "fecha": d["fecha"], "desc": d["descripcion"]})
                    icono = "💸" if d["tipo"] == "gasto" else "💰"
                    return await enviar(f"{icono} {d['tipo'].capitalize()} de {fmt_cop(d['monto'])} "
                                        f"({d['fecha']}). ¿En qué categoría?", reply_markup=kb_categorias(d["tipo"]))
                t, kb = self._ejecutar(d, uid, chat_id)
                if d["intent"] != "reiniciar":  # guarda contexto para que la IA entienda el hilo
                    self.db.add_mensaje(uid, "user", texto)
                    self.db.add_mensaje(uid, "assistant", t)
                return await enviar(t, reply_markup=kb)

        # 5. Conversación libre con IA
        if not usar_ia:
            return await enviar(SIN_IA_PREGUNTA if es_pregunta(texto) else SIN_IA, reply_markup=menu_inline(False))
        if not self.limite.permitir(uid):
            return await enviar("Vas muy rápido 😅 Dame un minuto y seguimos.")
        hoy = self.servicio.hoy().isoformat()
        if self.cfg.llm_max_dia_total and self.db.uso_ia_total(hoy) >= self.cfg.llm_max_dia_total:
            return await enviar("El asistente de IA llegó a su límite de uso de hoy. Mañana vuelve a estar "
                                "disponible; mientras tanto, los botones y las frases como «gasté 20 mil en "
                                "comida» siguen funcionando.", reply_markup=menu_inline(False))
        if self.cfg.llm_max_por_dia and self.db.uso_ia(uid, hoy) >= self.cfg.llm_max_por_dia:
            return await enviar(f"Hoy ya usaste tus {self.cfg.llm_max_por_dia} mensajes con la IA 🙂. Mañana "
                                "seguimos conversando; los botones y las frases directas siguen funcionando.",
                                reply_markup=menu_inline(False))
        self.db.sumar_uso_ia(uid, hoy)
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
        try:
            respuesta = await self.asistente.responder(uid, chat_id, nombre, texto)
        except Exception:
            log.exception("Fallo del asistente IA")
            return await enviar(
                "Ahora mismo no puedo conversar con mi cerebro de IA 😕. "
                "Los botones y comandos básicos siguen funcionando:",
                reply_markup=menu_inline(True),
            )
        await enviar(respuesta)

    # ------------------------------------------------------------------- voz
    async def on_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizado(update.effective_user.id):
            return
        if not self._acepto(update.effective_user.id):
            self._registrar_usuario(update)
            return await self._pedir_privacidad(lambda t, **kw: self._responder(update, t, **kw))
        if not voz.disponible():
            return await self._responder(
                update, "Aún no puedo escuchar audios en este servidor 🎤. Escríbeme el mensaje, por favor."
            )
        nota = update.message.voice
        if nota.duration and nota.duration > 90:
            return await self._responder(update, "El audio es muy largo. Mándame uno de menos de 90 segundos.")
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        fd, ruta = tempfile.mkstemp(suffix=".ogg")
        os.close(fd)
        try:
            archivo = await nota.get_file()
            await archivo.download_to_drive(ruta)
            texto = await voz.transcribir(ruta, self.cfg.whisper_model)
        except Exception:
            log.exception("Fallo al transcribir audio")
            return await self._responder(update, "No pude entender el audio 😕. ¿Lo intentas de nuevo o me escribes?")
        finally:
            try:
                os.remove(ruta)
            except OSError:
                pass
        if not texto:
            return await self._responder(update, "No logré escuchar nada en el audio. Inténtalo otra vez.")
        await self._responder(update, f"🎤 Entendí: «{texto}»")
        await self._procesar(update, context, texto)

    # ------------------------------------------------------------- callbacks
    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not self._autorizado(q.from_user.id):
            return await q.answer()
        await q.answer()
        uid, chat_id, nombre = q.from_user.id, q.message.chat_id, q.from_user.first_name
        self.db.upsert_usuario(uid, nombre, chat_id)
        accion, ud = (q.data or ""), context.user_data
        enviar: Enviar = lambda t, **kw: _enviar_msg(q.message, t, **kw)  # noqa: E731

        if accion == "priv:si":
            self.db.registrar_aceptacion(uid, self.servicio.ahora().isoformat(timespec="minutes"))
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            await enviar("✅ Gracias. Guardé tu autorización.")
            return await self._bienvenida(enviar, nombre)
        if accion == "priv:no":
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            return await enviar(SIN_AUTORIZACION)
        if not self._acepto(uid):
            return await self._pedir_privacidad(enviar)

        if accion.startswith("menu:"):
            return await self._accion(accion[5:], uid, chat_id, nombre, ud, enviar)

        if accion.startswith("f:"):  # paso de un flujo guiado
            fl = ud.get("flujo")
            try:  # quita los botones del mensaje ya contestado para evitar doble toque
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            if not fl or time.monotonic() - fl["t"] > FLUJO_TTL:
                ud.pop("flujo", None)
                return await enviar("Esa opción ya expiró ⏳ Elige de nuevo desde el menú:",
                                    reply_markup=menu_inline(self.asistente.disponible))
            return await self._flujo(ud, accion[2:], True, uid, chat_id, enviar)

        if accion.startswith("t:"):
            r = self.servicio.completar_tarea(uid, accion[2:])
            msg = r["error"] if "error" in r else f"🎉 ¡Tarea {r['num']} completada: {r['descripcion']}!"
            lista = self.servicio.listar_tareas(uid)
            kb = None
            if lista["tareas"]:
                b = [(f"✅ {t['num']}", f"t:{t['num']}") for t in lista["tareas"][:12]]
                kb = _kb([b[i:i + 4] for i in range(0, len(b), 4)])
            await q.edit_message_text(f"{msg}\n\n{formato.tareas(lista)}", reply_markup=kb)
        elif accion.startswith("r:"):
            r = self.servicio.borrar_recordatorio(uid, accion[2:])
            msg = r["error"] if "error" in r else f"🗑 Recordatorio {r['num']} eliminado."
            lista = self.servicio.listar_recordatorios(uid)
            kb = None
            if lista["recordatorios"]:
                b = [(f"🗑 {x['num']}", f"r:{x['num']}") for x in lista["recordatorios"][:12]]
                kb = _kb([b[i:i + 4] for i in range(0, len(b), 4)])
            await q.edit_message_text(f"{msg}\n\n{formato.recordatorios(lista)}", reply_markup=kb)
        elif accion.startswith("p:"):
            r = self.servicio.borrar_presupuesto(uid, accion[2:])
            msg = r["error"] if "error" in r else f"🗑 Quité el presupuesto de {r['categoria']}."
            texto, kb = self._vista_presupuestos(uid)
            await q.edit_message_text(f"{msg}\n\n{texto}", reply_markup=kb)
        elif accion == "reset:si":
            self.servicio.reiniciar(uid)
            ud.pop("flujo", None)
            await q.edit_message_text("♻ Listo, borré todos tus datos. Empezamos de cero.")
        elif accion == "reset:no":
            await q.edit_message_text("Cancelado: no borré nada. 👍")

    # ---------------------------------------------------------------- errores
    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("Error no controlado", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text("Ups, algo salió mal de mi lado. Inténtalo de nuevo 🙏")
            except Exception:
                pass


def main() -> None:
    configurar_logs()
    cfg = Config.desde_entorno()
    bot = HelAssistBot(cfg)
    app = bot.construir()
    log.info("Iniciando HelAssist AI...")
    app.run_polling(drop_pending_updates=True, bootstrap_retries=-1)  # reintenta si falla la red al arrancar
