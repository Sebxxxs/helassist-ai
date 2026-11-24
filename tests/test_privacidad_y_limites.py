"""Autorización de datos, conteo de uso de la IA y cache de prompts (sin Telegram)."""
import asyncio
import sqlite3
from types import SimpleNamespace as NS

from helassist.ai import HERRAMIENTAS, HERRAMIENTAS_API, PROMPT_FIJO, Asistente, bloques_sistema
from helassist.db import Database


def test_autorizacion_de_datos(db):
    assert not db.acepto_privacidad(5)
    db.upsert_usuario(5, "Ana", 105)
    assert not db.acepto_privacidad(5)
    db.registrar_aceptacion(5, "2026-11-20T10:00")
    assert db.acepto_privacidad(5)
    assert db.usuario(5)["nombre"] == "Ana"  # registrar la autorización no borra el nombre
    db.upsert_usuario(5, "Ana María", 105)
    assert db.acepto_privacidad(5)  # actualizar el nombre no borra la autorización


def test_base_antigua_recibe_la_columna_de_autorizacion(tmp_path):
    ruta = str(tmp_path / "vieja.db")
    con = sqlite3.connect(ruta)
    con.execute("CREATE TABLE usuarios (user_id INTEGER PRIMARY KEY, nombre TEXT, chat_id INTEGER, "
                "creado TEXT DEFAULT CURRENT_TIMESTAMP)")
    con.execute("INSERT INTO usuarios (user_id, nombre, chat_id) VALUES (1, 'Luis', 101)")
    con.commit()
    con.close()
    db = Database(ruta)
    assert not db.acepto_privacidad(1)
    db.registrar_aceptacion(1, "2026-11-20T10:00")
    assert db.acepto_privacidad(1) and db.usuario(1)["nombre"] == "Luis"


def test_conteo_de_uso_de_ia_por_usuario_y_total(db):
    for _ in range(3):
        db.sumar_uso_ia(1, "2026-11-20")
    db.sumar_uso_ia(2, "2026-11-20")
    db.sumar_uso_ia(1, "2026-11-21")
    assert db.uso_ia(1, "2026-11-20") == 3 and db.uso_ia(2, "2026-11-20") == 1
    assert db.uso_ia_total("2026-11-20") == 4 and db.uso_ia_total("2026-11-22") == 0


def test_reiniciar_no_borra_el_conteo_de_ia(servicio, db):
    db.sumar_uso_ia(1, "2026-11-20")
    servicio.reiniciar(1)
    assert db.uso_ia(1, "2026-11-20") == 1  # así borrar los datos no sirve para saltarse el límite


def test_herramientas_con_cache_sin_tocar_las_originales():
    assert "cache_control" not in HERRAMIENTAS[-1]
    assert HERRAMIENTAS_API[-1]["cache_control"] == {"type": "ephemeral"}
    assert [h["name"] for h in HERRAMIENTAS_API] == [h["name"] for h in HERRAMIENTAS]
    assert all("cache_control" not in h for h in HERRAMIENTAS_API[:-1])


def test_bloques_de_sistema_separan_lo_fijo_de_lo_que_cambia(servicio):
    b = bloques_sistema("Luis", servicio.ahora(), servicio.resumen_mes(1))
    assert b[0]["text"] == PROMPT_FIJO and b[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in b[1] and "Luis" in b[1]["text"] and "Hoy es" in b[1]["text"]
    assert "Luis" not in PROMPT_FIJO and "Hoy es" not in PROMPT_FIJO  # lo fijo no cambia entre usuarios


def test_la_peticion_a_la_api_usa_el_cache(servicio, db, cfg):
    peticiones = []

    async def crear(**kw):
        peticiones.append(kw)
        return NS(stop_reason="end_turn", content=[NS(type="text", text="Hola")])

    a = Asistente(servicio, db, cfg, NS(messages=NS(create=crear)))
    asyncio.run(a.responder(1, 10, "Luis", "hola"))
    assert peticiones[0]["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert peticiones[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
