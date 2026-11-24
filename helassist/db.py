"""Capa de acceso a datos (SQLite).

Cambios respecto a la versión anterior:
- Todo está separado por usuario (user_id): nadie ve los datos de otro.
- Una sola tabla de movimientos (gasto/ingreso) con categoría.
- Montos como enteros (pesos) para evitar errores de redondeo de REAL.
- Conexiones con context manager: siempre se cierran, con commit/rollback.
- Recordatorios y tareas persistentes (sobreviven a reinicios).
"""
from __future__ import annotations

import glob
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    user_id  INTEGER PRIMARY KEY,
    nombre   TEXT,
    chat_id  INTEGER,
    creado   TEXT DEFAULT CURRENT_TIMESTAMP,
    acepto_privacidad TEXT
);
CREATE TABLE IF NOT EXISTS movimientos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    tipo        TEXT NOT NULL CHECK (tipo IN ('gasto','ingreso')),
    monto       INTEGER NOT NULL CHECK (monto > 0),
    categoria   TEXT NOT NULL DEFAULT 'otros',
    descripcion TEXT,
    fecha       TEXT NOT NULL,
    creado      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mov_user_fecha ON movimientos (user_id, fecha);
CREATE TABLE IF NOT EXISTS tareas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    num         INTEGER NOT NULL,
    descripcion TEXT NOT NULL,
    fecha       TEXT,
    completada  INTEGER NOT NULL DEFAULT 0,
    creado      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, num)
);
CREATE TABLE IF NOT EXISTS recordatorios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    chat_id     INTEGER NOT NULL,
    num         INTEGER NOT NULL,
    mensaje     TEXT NOT NULL,
    fecha_hora  TEXT NOT NULL,
    repeticion  TEXT,
    enviado     INTEGER NOT NULL DEFAULT 0,
    creado      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, num)
);
CREATE TABLE IF NOT EXISTS mensajes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL,
    rol       TEXT NOT NULL CHECK (rol IN ('user','assistant')),
    contenido TEXT NOT NULL,
    creado    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_msg_user ON mensajes (user_id, id);
CREATE TABLE IF NOT EXISTS uso_ia (
    fecha    TEXT NOT NULL,
    user_id  INTEGER NOT NULL,
    mensajes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (fecha, user_id)
);
CREATE TABLE IF NOT EXISTS presupuestos (
    user_id   INTEGER NOT NULL,
    categoria TEXT NOT NULL,
    monto     INTEGER NOT NULL CHECK (monto > 0),
    PRIMARY KEY (user_id, categoria)
);
"""

MAX_HISTORIAL = 200


def _dicts(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


class Database:
    def __init__(self, path: str):
        self.path = path
        self.init()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            # Bases creadas con versiones anteriores: agregar la columna de autorización
            columnas = {r["name"] for r in c.execute("PRAGMA table_info(usuarios)")}
            if "acepto_privacidad" not in columnas:
                c.execute("ALTER TABLE usuarios ADD COLUMN acepto_privacidad TEXT")

    # ---------------------------------------------------------------- usuarios
    def upsert_usuario(self, user_id: int, nombre: str | None, chat_id: int | None) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO usuarios (user_id, nombre, chat_id) VALUES (?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET nombre=excluded.nombre, chat_id=excluded.chat_id""",
                (user_id, nombre, chat_id),
            )

    def acepto_privacidad(self, user_id: int) -> bool:
        with self._conn() as c:
            r = c.execute("SELECT acepto_privacidad FROM usuarios WHERE user_id=?", (user_id,)).fetchone()
            return bool(r and r["acepto_privacidad"])

    def registrar_aceptacion(self, user_id: int, fecha_hora: str) -> None:
        """Guarda cuándo el usuario autorizó el tratamiento de sus datos (prueba de la autorización)."""
        with self._conn() as c:
            c.execute(
                """INSERT INTO usuarios (user_id, acepto_privacidad) VALUES (?,?)
                   ON CONFLICT(user_id) DO UPDATE SET acepto_privacidad=excluded.acepto_privacidad""",
                (user_id, fecha_hora),
            )

    # ------------------------------------------------------------- uso de la IA
    def uso_ia(self, user_id: int, fecha: str) -> int:
        with self._conn() as c:
            r = c.execute("SELECT mensajes FROM uso_ia WHERE fecha=? AND user_id=?", (fecha, user_id)).fetchone()
            return r["mensajes"] if r else 0

    def uso_ia_total(self, fecha: str) -> int:
        with self._conn() as c:
            r = c.execute("SELECT COALESCE(SUM(mensajes), 0) AS n FROM uso_ia WHERE fecha=?", (fecha,)).fetchone()
            return int(r["n"])

    def sumar_uso_ia(self, user_id: int, fecha: str) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO uso_ia (fecha, user_id, mensajes) VALUES (?,?,1)
                   ON CONFLICT(fecha, user_id) DO UPDATE SET mensajes = mensajes + 1""",
                (fecha, user_id),
            )

    def usuario(self, user_id: int) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM usuarios WHERE user_id=?", (user_id,)).fetchone()
            return dict(r) if r else None

    # ------------------------------------------------------------- movimientos
    def add_movimiento(
        self, user_id: int, tipo: str, monto: int, categoria: str, descripcion: str | None, fecha: str
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO movimientos (user_id, tipo, monto, categoria, descripcion, fecha)
                   VALUES (?,?,?,?,?,?)""",
                (user_id, tipo, int(monto), categoria, descripcion, fecha),
            )
            return int(cur.lastrowid)

    def movimientos(
        self,
        user_id: int,
        tipo: str | None = None,
        desde: str | None = None,
        hasta: str | None = None,
        categoria: str | None = None,
        limite: int | None = None,
    ) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM movimientos WHERE user_id=?", [user_id]
        if tipo:
            sql += " AND tipo=?"
            params.append(tipo)
        if desde:
            sql += " AND fecha>=?"
            params.append(desde)
        if hasta:
            sql += " AND fecha<=?"
            params.append(hasta)
        if categoria:
            sql += " AND categoria=?"
            params.append(categoria)
        sql += " ORDER BY fecha DESC, id DESC"
        if limite:
            sql += " LIMIT ?"
            params.append(int(limite))
        with self._conn() as c:
            return _dicts(c.execute(sql, params))

    def totales_por_categoria(
        self, user_id: int, desde: str | None = None, hasta: str | None = None
    ) -> list[dict[str, Any]]:
        sql, params = (
            "SELECT tipo, categoria, SUM(monto) AS total, COUNT(*) AS n FROM movimientos WHERE user_id=?",
            [user_id],
        )
        if desde:
            sql += " AND fecha>=?"
            params.append(desde)
        if hasta:
            sql += " AND fecha<=?"
            params.append(hasta)
        sql += " GROUP BY tipo, categoria ORDER BY total DESC"
        with self._conn() as c:
            return _dicts(c.execute(sql, params))

    def ultimo_movimiento(self, user_id: int) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute(
                "SELECT * FROM movimientos WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)
            ).fetchone()
            return dict(r) if r else None

    def borrar_movimiento(self, user_id: int, mov_id: int) -> bool:
        with self._conn() as c:
            return c.execute(
                "DELETE FROM movimientos WHERE id=? AND user_id=?", (mov_id, user_id)
            ).rowcount > 0

    # ------------------------------------------------------------------ tareas
    @staticmethod
    def _siguiente_num(c: sqlite3.Connection, tabla: str, user_id: int) -> int:
        r = c.execute(f"SELECT COALESCE(MAX(num),0)+1 AS n FROM {tabla} WHERE user_id=?", (user_id,))
        return int(r.fetchone()["n"])

    def add_tarea(self, user_id: int, descripcion: str, fecha: str | None) -> int:
        with self._conn() as c:
            num = self._siguiente_num(c, "tareas", user_id)
            c.execute(
                "INSERT INTO tareas (user_id, num, descripcion, fecha) VALUES (?,?,?,?)",
                (user_id, num, descripcion, fecha),
            )
            return num

    def tareas(self, user_id: int, incluir_completadas: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tareas WHERE user_id=?"
        if not incluir_completadas:
            sql += " AND completada=0"
        sql += " ORDER BY completada, fecha IS NULL, fecha, num"
        with self._conn() as c:
            return _dicts(c.execute(sql, (user_id,)))

    def get_tarea(self, user_id: int, num: int) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM tareas WHERE user_id=? AND num=?", (user_id, num)).fetchone()
            return dict(r) if r else None

    def completar_tarea(self, user_id: int, num: int) -> bool:
        with self._conn() as c:
            return c.execute(
                "UPDATE tareas SET completada=1 WHERE user_id=? AND num=? AND completada=0",
                (user_id, num),
            ).rowcount > 0

    # ----------------------------------------------------------- recordatorios
    def add_recordatorio(
        self, user_id: int, chat_id: int, mensaje: str, fecha_hora: str, repeticion: str | None
    ) -> dict[str, Any]:
        with self._conn() as c:
            num = self._siguiente_num(c, "recordatorios", user_id)
            cur = c.execute(
                """INSERT INTO recordatorios (user_id, chat_id, num, mensaje, fecha_hora, repeticion)
                   VALUES (?,?,?,?,?,?)""",
                (user_id, chat_id, num, mensaje, fecha_hora, repeticion),
            )
            return dict(c.execute("SELECT * FROM recordatorios WHERE id=?", (cur.lastrowid,)).fetchone())

    def get_recordatorio(self, rec_id: int) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM recordatorios WHERE id=?", (rec_id,)).fetchone()
            return dict(r) if r else None

    def recordatorios(self, user_id: int | None = None) -> list[dict[str, Any]]:
        """Recordatorios pendientes (no enviados). Sin user_id devuelve los de todos."""
        sql, params = "SELECT * FROM recordatorios WHERE enviado=0", []
        if user_id is not None:
            sql += " AND user_id=?"
            params.append(user_id)
        sql += " ORDER BY fecha_hora"
        with self._conn() as c:
            return _dicts(c.execute(sql, params))

    def marcar_enviado(self, rec_id: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE recordatorios SET enviado=1 WHERE id=?", (rec_id,))

    def reprogramar(self, rec_id: int, fecha_hora: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE recordatorios SET fecha_hora=?, enviado=0 WHERE id=?", (fecha_hora, rec_id))

    def borrar_recordatorio(self, user_id: int, num: int) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute(
                "SELECT * FROM recordatorios WHERE user_id=? AND num=? AND enviado=0", (user_id, num)
            ).fetchone()
            if not r:
                return None
            c.execute("DELETE FROM recordatorios WHERE id=?", (r["id"],))
            return dict(r)

    # --------------------------------------------------------------- historial
    def add_mensaje(self, user_id: int, rol: str, contenido: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO mensajes (user_id, rol, contenido) VALUES (?,?,?)",
                (user_id, rol, contenido[:4000]),
            )
            c.execute(
                """DELETE FROM mensajes WHERE user_id=? AND id NOT IN
                   (SELECT id FROM mensajes WHERE user_id=? ORDER BY id DESC LIMIT ?)""",
                (user_id, user_id, MAX_HISTORIAL),
            )

    def ultimos_mensajes(self, user_id: int, n: int = 12) -> list[dict[str, str]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT rol, contenido FROM mensajes WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, n),
            ).fetchall()
        msgs = [{"role": r["rol"], "content": r["contenido"]} for r in reversed(rows)]
        while msgs and msgs[0]["role"] != "user":  # la API exige empezar con "user"
            msgs.pop(0)
        return msgs

    # ------------------------------------------------------------ presupuestos
    def total_gastado(self, user_id: int, categoria: str, desde: str, hasta: str) -> int:
        with self._conn() as c:
            r = c.execute(
                "SELECT COALESCE(SUM(monto),0) FROM movimientos WHERE user_id=? AND tipo='gasto' "
                "AND categoria=? AND fecha>=? AND fecha<=?",
                (user_id, categoria, desde, hasta),
            ).fetchone()
            return int(r[0])

    def set_presupuesto(self, user_id: int, categoria: str, monto: int) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO presupuestos (user_id, categoria, monto) VALUES (?,?,?)
                   ON CONFLICT(user_id, categoria) DO UPDATE SET monto=excluded.monto""",
                (user_id, categoria, int(monto)),
            )

    def get_presupuesto(self, user_id: int, categoria: str) -> int | None:
        with self._conn() as c:
            r = c.execute(
                "SELECT monto FROM presupuestos WHERE user_id=? AND categoria=?", (user_id, categoria)
            ).fetchone()
            return int(r[0]) if r else None

    def presupuestos(self, user_id: int) -> list[dict[str, Any]]:
        with self._conn() as c:
            return _dicts(c.execute(
                "SELECT categoria, monto FROM presupuestos WHERE user_id=? ORDER BY categoria", (user_id,)))

    def borrar_presupuesto(self, user_id: int, categoria: str) -> bool:
        with self._conn() as c:
            return c.execute(
                "DELETE FROM presupuestos WHERE user_id=? AND categoria=?", (user_id, categoria)
            ).rowcount > 0

    # --------------------------------------------------------------- respaldo
    def respaldar(self, carpeta: str, conservar: int = 7) -> str:
        """Copia consistente de la base (API de backup de SQLite). Una por día; conserva las últimas N."""
        from datetime import datetime

        os.makedirs(carpeta, exist_ok=True)
        destino = os.path.join(carpeta, datetime.now().strftime("helassist-%Y%m%d.db"))
        tmp = destino + ".tmp"
        origen, copia = sqlite3.connect(self.path, timeout=10), sqlite3.connect(tmp)
        try:
            origen.backup(copia)
        finally:
            copia.close()
            origen.close()
        os.replace(tmp, destino)
        for viejo in sorted(glob.glob(os.path.join(carpeta, "helassist-*.db")))[:-max(1, conservar)]:
            try:
                os.remove(viejo)
            except OSError:
                pass
        return destino

    # ------------------------------------------------------------------- reset
    def reiniciar_usuario(self, user_id: int) -> dict[str, int]:
        with self._conn() as c:
            out = {}
            for tabla in ("movimientos", "tareas", "recordatorios", "mensajes", "presupuestos"):
                out[tabla] = c.execute(f"DELETE FROM {tabla} WHERE user_id=?", (user_id,)).rowcount
            return out
