from datetime import date, datetime

import pytest

from helassist.parser import (
    detectar_intencion,
    es_pregunta,
    extraer_cantidad,
    fmt_cop,
    normalizar_categoria,
    parse_movimiento,
    parse_recordatorio,
    parse_tarea,
    siguiente_fecha,
)

AHORA = datetime(2026, 11, 9, 20, 0)  # lunes
HOY = AHORA.date()


@pytest.mark.parametrize("texto,esperado", [
    ("20 mil", 20000), ("1.500.000", 1500000), ("3 millones", 3000000), ("1,5 millones", 1500000),
    ("300000", 300000), ("$20.000", 20000), ("20k", 20000), ("veinte mil", 20000),
    ("dos millones quinientos mil", 2500000), ("20 lucas", 20000), ("treinta y cinco mil", 35000),
    ("compré 2 camisas por 60 mil", 60000), ("pagué 50000 el 15 de mayo", 50000),
    ("el 10/03", None), ("a las 8:30 pm", None), ("hola", None), ("compré una camisa", None),
])
def test_cantidades(texto, esperado):
    assert extraer_cantidad(texto) == esperado


def test_formato_pesos():
    assert fmt_cop(1500000) == "$1.500.000"
    assert fmt_cop(0) == "$0"


@pytest.mark.parametrize("texto,tipo,monto,cat", [
    ("Gasté 20 mil en comida", "gasto", 20000, "comida"),
    ("Ingresé 500 mil en salario", "ingreso", 500000, "salario"),
    ("Recibí 200000 en freelance", "ingreso", 200000, "freelance"),
    ("Compré ropa por 60 mil", "gasto", 60000, "ropa"),
    ("Pagué 100000 en transporte", "gasto", 100000, "transporte"),
    ("gasté 15 mil en uber", "gasto", 15000, "transporte"),
    ("me pagaron la quincena 70000", "ingreso", 70000, "salario"),
    ("gasté en el almuerzo con mis amigos 45.000", "gasto", 45000, "comida"),
])
def test_movimientos(texto, tipo, monto, cat):
    m = parse_movimiento(texto, HOY)
    assert (m["tipo"], m["monto"], m["categoria"]) == (tipo, monto, cat)


def test_movimiento_con_fecha_relativa():
    assert parse_movimiento("gasté 15 mil en uber ayer", HOY)["fecha"] == "2026-11-08"


@pytest.mark.parametrize("texto", ["hola cómo estás", "gasté mucho este mes", "pasajes 30000", "cuánto es 20 mil"])
def test_no_es_movimiento(texto):
    assert parse_movimiento(texto, HOY) is None


def test_categorias():
    assert normalizar_categoria("Uber") == "transporte"
    assert normalizar_categoria("pasajes") == "transporte"
    assert normalizar_categoria("Mascotas") == "mascotas"
    assert normalizar_categoria(None) == "otros"


def test_recordatorio_manana_con_hora():
    r = parse_recordatorio("Recuérdame pagar Netflix mañana a las 8:30", AHORA)
    assert r["mensaje"] == "pagar Netflix"
    assert r["fecha_hora"] == datetime(2026, 11, 10, 8, 30)


def test_recordatorio_dia_semana_y_acentos_se_conservan():
    r = parse_recordatorio("Avísame llamar a mamá el viernes", AHORA)
    assert r["mensaje"] == "llamar a mamá"
    assert r["fecha_hora"] == datetime(2026, 11, 13, 9, 0)


def test_recordatorio_fecha_pasada_salta_al_proximo_anio():
    assert parse_recordatorio("recuérdame pagar el 10/03", AHORA)["fecha_hora"] == datetime(2027, 3, 10, 9, 0)


def test_recordatorio_en_la_manana_no_es_manana_dia():
    r = parse_recordatorio("recuérdame llamar a las 7 de la mañana", AHORA)
    assert r["fecha_hora"] == datetime(2026, 11, 10, 7, 0)  # 7am ya pasó hoy → mañana


def test_recordatorio_recurrente():
    r = parse_recordatorio("recuérdame pagar arriendo cada mes el 5", AHORA)
    assert r["repeticion"] == "mensual" and r["fecha_hora"] == datetime(2026, 12, 5, 9, 0)


def test_recordatorio_pm_y_sin_fecha():
    r = parse_recordatorio("recuérdame tomar agua a las 9 pm", AHORA)
    assert r["fecha_hora"] == datetime(2026, 11, 9, 21, 0)


def test_recordatorio_sin_cuando_devuelve_none():
    assert parse_recordatorio("recuérdame algo", AHORA) is None


def test_recordatorio_en_el_pasado():
    assert "error" in parse_recordatorio("recuérdame algo hoy a las 8 am", AHORA)


def test_tarea():
    assert parse_tarea("Crear tarea estudiar mañana", HOY) == {"descripcion": "estudiar", "fecha": "2026-11-10"}
    assert parse_tarea("crear tarea entregar el trabajo", HOY)["fecha"] is None


def test_siguiente_fecha_mensual_ajusta_fin_de_mes():
    dt = datetime(2026, 1, 31, 9, 0)
    assert siguiente_fecha(dt, "mensual", datetime(2026, 1, 31, 9, 1)) == datetime(2026, 2, 28, 9, 0)


def test_siguiente_fecha_salta_si_el_bot_estuvo_apagado():
    dt = datetime(2026, 11, 1, 9, 0)
    assert siguiente_fecha(dt, "semanal", datetime(2026, 11, 20, 0, 0)) == datetime(2026, 11, 22, 9, 0)


@pytest.mark.parametrize("texto,intent", [
    ("completar tarea 3", "completar_tarea"), ("ver tareas", "ver_tareas"), ("mis recordatorios", "ver_recordatorios"),
    ("resumen", "resumen"), ("análisis", "analisis"), ("reporte mensual", "reporte"), ("reiniciar", "reiniciar"),
    ("deshacer", "deshacer"), ("Gasté 20 mil en comida", "movimiento"), ("recuérdame algo", "recordatorio_incompleto"),
    ("crear tarea estudiar", "tarea"), ("borrar recordatorio 2", "borrar_recordatorio"),
])
def test_intenciones(texto, intent):
    assert detectar_intencion(texto, AHORA)["intent"] == intent


@pytest.mark.parametrize("texto", ["cuéntame un chiste", "¿qué es un CDT?", "quiero aprender inglés", "cuánto cuesta un cdt"])
def test_sin_intencion_clara(texto):
    assert detectar_intencion(texto, AHORA) is None


def test_es_pregunta():
    assert es_pregunta("¿Cuánto gasté este mes?") and es_pregunta("cuánto llevo en comida")
    assert not es_pregunta("gasté 20 mil en comida")


@pytest.mark.parametrize("texto", [
    "recuerdame pagar netflix el 6 de cada mes",
    "Recuérdame pagar Netflix el día 6 de cada mes",
    "recuérdame pagar netflix los 6 de cada mes",
])
def test_recordatorio_dia_fijo_de_cada_mes(texto):
    r = parse_recordatorio(texto, AHORA)
    assert r["mensaje"] == "pagar Netflix".lower() or r["mensaje"].lower() == "pagar netflix"
    assert r["repeticion"] == "mensual" and r["fecha_hora"] == datetime(2026, 12, 6, 9, 0)
