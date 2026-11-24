# HelAssist AI (v3.1)

Proyecto de la asignatura Formulación y Evaluación de Proyectos, Programa de Ingeniería de Sistemas,
Universidad Libre, seccional Barranquilla (2026-2). Docente: José William Penagos Vargas.

Equipo: Sebastián Andrés Palomino (gerencia y producto), Luis Felipe Lora (desarrollo e infraestructura),
Santiago Baquero (datos, IA y calidad) y Gabriel Rojas (comercial y soporte).

Este repositorio es el proyecto funcional que acompaña los estudios de mercado, técnico y económico.
El manual de usuario está en [docs/Manual_de_Usuario_HelAssist_AI.pdf](docs/Manual_de_Usuario_HelAssist_AI.pdf).
El bot es un prototipo: todavía no se ha probado con usuarios reales.

Asistente de finanzas personales, tareas y recordatorios para Telegram. Entiende texto y notas de voz,
y **conversa de forma natural**: además de las opciones del menú, responde preguntas libres usando Claude.

## Versión 2: qué cambió respecto a la versión anterior

**Errores corregidos**
- `financial_functions.py` y `tasks.py` no importaban lo que usaban (`NameError`).
- `init_db()` nunca se llamaba: las tablas no existían.
- `main.py` creaba el `Updater` dos veces y usaba la API v13 de python-telegram-bot (obsoleta).
- `reminders.py` llamaba a `scheduler.start()` en cada recordatorio (fallaba desde el segundo).
- `ingreso_mensual = 1000` estaba fijo; ahora se calcula con los ingresos reales.
- División por cero cuando no había ingresos.
- `/registrargasto` cortaba la descripción en la primera palabra.
- Todos los usuarios compartían los mismos gastos; ahora todo está separado por `user_id`.
- Había tres versiones distintas de `main`, `database` y `financial_functions`; ahora hay una sola.
- El token estaba escrito en el código (**revócalo en @BotFather**) → ahora va en `.env`.
- Los recordatorios se perdían al reiniciar → ahora se guardan en SQLite y se reprograman al arrancar.

**Nuevo**
- Conversación libre con IA (Claude) con herramientas: registra gastos, crea tareas y recordatorios, y
  consulta tus finanzas desde frases naturales («ayer me gasté como 40 lucas en el cine»).
- Reglas rápidas (gratis) para las frases claras; la IA solo se usa cuando hace falta.
- Montos: «20 mil», «1.500.000», «1,5 millones», «20k», «20 lucas», «veinte mil».
- Fechas y horas: «mañana», «el viernes», «10/03», «15 de mayo», «en 3 días», «a las 8 de la noche».
- Recordatorios recurrentes (diario, semanal, mensual).
- Botones: completar tarea, borrar recordatorio y confirmación antes de reiniciar todo.
- `/deshacer`, categorías automáticas, límite de mensajes por minuto, lista blanca de usuarios,
  logs con rotación (sin filtrar el token) y 90 pruebas automáticas.

## Botones y menú

- Teclado fijo abajo + `/menu` con botones dentro del chat: gasto, ingreso, tarea, recordatorio, tareas,
  recordatorios, resumen, análisis, reporte, movimientos, deshacer, ayuda (y «Preguntar» si hay IA).
- Gasto, ingreso, tarea y recordatorio se piden **paso a paso con botones** (categoría, fecha, hora, repetición).
  Se puede cancelar en cualquier momento y los flujos expiran a los 10 minutos.
- Si escribes un recordatorio sin fecha («recuérdame pagar Netflix») el bot te pregunta lo que falta.
- Atajos: `/gasto /ingreso /tarea /recordatorio /resumen /analisis /reporte /tareas /recordatorios /deshacer /cancelar`.

## Instalación

```bash
python -m venv venv && source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # Windows: copy .env.example .env
# edita .env: TELEGRAM_TOKEN y (opcional pero recomendado) ANTHROPIC_API_KEY
python main.py
```

- **Sin `ANTHROPIC_API_KEY`** el bot funciona solo con reglas (comandos y frases estructuradas).
- **Con ella** conversa libremente. El costo es bajo con el modelo por defecto (`claude-haiku-5-5`); puedes
  cambiarlo en `ANTHROPIC_MODEL`. `LLM_MAX_POR_MINUTO` limita el uso por usuario.
- **Voz (opcional):** `pip install openai-whisper` y tener `ffmpeg` instalado.
- **Bot privado:** pon tu ID de Telegram en `ALLOWED_USERS`.

## Estructura

```
main.py                 punto de entrada
helassist/
  config.py             variables de entorno
  db.py                 SQLite (movimientos, tareas, recordatorios, historial)
  parser.py             español coloquial → montos, fechas, horas, intenciones
  finanzas.py           resumen, análisis, reporte mensual
  servicio.py           lógica de negocio (la usan el bot y la IA)
  recordatorios.py      planificador persistente (APScheduler 3.x)
  ai.py                 asistente conversacional con herramientas
  voz.py                Whisper (opcional)
  formato.py            textos de respuesta
  bot.py                handlers de Telegram
tests/                  pytest
```

## Cómo decide el bot qué hacer con un mensaje

1. Botón del menú → respuesta directa.
2. Si es una pregunta y hay IA → IA.
3. Si es una frase con estructura clara (gasto, tarea, recordatorio, resumen...) → reglas (sin costo).
4. Todo lo demás → IA conversa y, si hace falta, usa herramientas sobre tus datos.
5. Sin IA configurada → mensaje de ayuda.

## Pruebas

```bash
pip install -r requirements-dev.txt
pytest
```

## Privacidad

Se guarda el ID de Telegram, tu nombre de pila y tus movimientos, tareas y recordatorios, todos separados por
usuario. Cuando la IA está activa, el texto de tus mensajes y los datos que consulta se envían a la API de
Anthropic para generar la respuesta. «♻ Reiniciar» borra todos tus datos. Haz copias de seguridad de
`helassist.db` y no la subas a GitHub.

## Novedades de la versión 3

- **Datos que se guardaban mal, corregidos:** varios gastos en un mensaje («gasté 20 mil en comida y 15 mil en
  transporte»), «el lunes gasté…» ahora guarda el lunes que ya pasó, «ver mis ingresos» ya no muestra gastos,
  los resúmenes respetan el período («resumen de la semana», «de octubre»), si falta la categoría el bot la
  pregunta con botones y los montos diminutos («2 pizzas») ya no se guardan como $2.
- **Frases nuevas:** «Netflix 30000», «salario 1500000», «me cayeron 200 mil», «tanqueé 20 mil», «tarea: …»,
  «tengo que …», saludos/gracias/«qué puedes hacer», consultas sin IA («¿cuánto gasté en comida este mes?»).
- **Recordatorios:** «en 30 minutos», «cada 2 horas», «todos los lunes a las 7 am», «pon una alarma a las 6»,
  y el día 29–31 de cada mes se ajusta en meses cortos (el 31 cae el 28/30 y vuelve al 31).
- **Presupuestos por categoría** (botón 💼 o «presupuesto de comida 500 mil»): avisa al 80 % y al 100 %.
- **Respaldo automático** de la base de datos al iniciar y cada día a las 3:00 en la carpeta `backups/`
  (conserva 7; configurable con `BACKUP_DIR` y `BACKUPS_CONSERVAR`).

## Novedades de la versión 3.1

- **Autorización de datos (Ley 1581 de 2012):** la primera vez que alguien escribe, el bot explica qué datos
  guarda y que, si usa la IA, sus mensajes se envían a Anthropic (Estados Unidos). Hasta que la persona toque
  «✅ Acepto», el bot no guarda ningún movimiento. La fecha de la autorización queda en la tabla `usuarios`.
  Para pruebas locales se puede apagar con `EXIGIR_AVISO_PRIVACIDAD=no`.
- **Cache de prompts:** las instrucciones fijas y las definiciones de herramientas se envían marcadas con
  `cache_control`, así la API las cobra a precio de lectura de cache. El contexto que cambia (fecha, nombre,
  cifras del mes) va en un bloque aparte para no romper el cache.
- **Límite diario de IA:** `LLM_MAX_POR_DIA` (por usuario, 40 por defecto) y `LLM_MAX_DIA_TOTAL` (todo el bot,
  3000 por defecto). El conteo se guarda en la base (tabla `uso_ia`), así que no se reinicia al apagar el bot.
  Cuando se alcanza un límite, los botones y las reglas siguen funcionando.
