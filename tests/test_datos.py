from datetime import datetime, timedelta

from helassist import finanzas


def test_aislamiento_entre_usuarios(servicio):
    servicio.registrar_movimiento(1, "gasto", 1000, "comida")
    servicio.registrar_movimiento(2, "gasto", 5000, "comida")
    assert servicio.consultar_finanzas(1)["gastos"] == 1000
    assert servicio.consultar_finanzas(2)["gastos"] == 5000
    servicio.crear_tarea(1, "solo mía")
    assert servicio.listar_tareas(2)["tareas"] == []


def test_validaciones_movimiento(servicio):
    assert "error" in servicio.registrar_movimiento(1, "regalo", 100)
    assert "error" in servicio.registrar_movimiento(1, "gasto", -5)
    assert "error" in servicio.registrar_movimiento(1, "gasto", "abc")
    assert "error" in servicio.registrar_movimiento(1, "gasto", 100, fecha="31/12/2026")
    assert servicio.registrar_movimiento(1, "gasto", 100, "Uber")["categoria"] == "transporte"


def test_balance_y_analisis(servicio):
    servicio.registrar_movimiento(1, "ingreso", 70000, "salario")
    servicio.registrar_movimiento(1, "gasto", 30000, "transporte")
    servicio.registrar_movimiento(1, "gasto", 30000, "transporte")
    r = servicio.resumen_mes(1)
    assert (r["ingresos"], r["gastos"], r["balance"]) == (70000, 60000, 10000)
    a = finanzas.analisis(r)
    assert a["nivel"] == "alerta" and a["categoria_mayor_gasto"]["categoria"] == "transporte"


def test_analisis_sin_ingresos_no_divide_por_cero(servicio):
    servicio.registrar_movimiento(1, "gasto", 100, "comida")
    a = servicio.analisis(1)
    assert a["nivel"] == "sin_ingresos" and a["ratio"] is None


def test_deshacer(servicio):
    assert "error" in servicio.deshacer(1)
    servicio.registrar_movimiento(1, "gasto", 100, "comida")
    servicio.registrar_movimiento(1, "gasto", 200, "comida")
    servicio.deshacer(1)
    assert servicio.consultar_finanzas(1)["gastos"] == 100


def test_tareas_numeracion_por_usuario(servicio):
    assert servicio.crear_tarea(1, "a")["num"] == 1
    assert servicio.crear_tarea(1, "b")["num"] == 2
    assert servicio.crear_tarea(2, "c")["num"] == 1
    assert servicio.completar_tarea(1, 2)["ok"]
    assert "error" in servicio.completar_tarea(1, 2)  # ya completada
    assert "error" in servicio.completar_tarea(2, 2)  # no es suya
    assert [t["num"] for t in servicio.listar_tareas(1)["tareas"]] == [1]


def test_reporte_mensual(servicio):
    servicio.registrar_movimiento(1, "ingreso", 1000000, "salario")
    servicio.registrar_movimiento(1, "gasto", 200000, "comida")
    rep = servicio.reporte_mensual(1)
    assert rep["balance"] == 800000 and rep["analisis"]["nivel"] == "excelente"


def test_reiniciar_borra_solo_al_usuario(servicio):
    servicio.registrar_movimiento(1, "gasto", 100)
    servicio.registrar_movimiento(2, "gasto", 100)
    servicio.reiniciar(1)
    assert servicio.consultar_finanzas(1)["gastos"] == 0
    assert servicio.consultar_finanzas(2)["gastos"] == 100


def test_historial_empieza_con_user_y_se_poda(db):
    for i in range(3):
        db.add_mensaje(1, "assistant", f"a{i}")
    db.add_mensaje(1, "user", "u")
    db.add_mensaje(1, "assistant", "r")
    msgs = db.ultimos_mensajes(1, 12)
    assert msgs[0]["role"] == "user" and len(msgs) == 2


def test_recordatorio_en_pasado_se_rechaza(servicio):
    r = servicio.crear_recordatorio(1, 99, "x", servicio.ahora() - timedelta(minutes=1))
    assert "error" in r


def test_recordatorio_texto_para_ia(servicio):
    manana = (servicio.hoy() + timedelta(days=1)).isoformat()
    r = servicio.crear_recordatorio_texto(1, 99, "pagar luz", manana, "08:30", "ninguna")
    assert r["ok"] and r["fecha_hora"].endswith("T08:30") and r["repeticion"] is None
    assert "error" in servicio.crear_recordatorio_texto(1, 99, "x", "mañana", "08:30", None)
    assert len(servicio.listar_recordatorios(1)["recordatorios"]) == 1
    assert servicio.borrar_recordatorio(1, 1)["ok"]
    assert servicio.listar_recordatorios(1)["recordatorios"] == []
