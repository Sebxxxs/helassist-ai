"""Configuración leída desde variables de entorno (o archivo .env).

Nunca escribas el token en el código: usa el archivo .env (ver .env.example).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

try:  # python-dotenv es opcional
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _enteros(valor: str) -> frozenset[int]:
    return frozenset(int(x) for x in valor.replace(";", ",").split(",") if x.strip().isdigit())


@dataclass(frozen=True)
class Config:
    telegram_token: str
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-haiku-5-5"
    db_path: str = "helassist.db"
    timezone: str = "America/Bogota"
    whisper_model: str = "base"
    allowed_users: frozenset[int] = frozenset()
    llm_max_por_minuto: int = 12
    llm_max_por_dia: int = 40  # mensajes a la IA por usuario y día (0 = sin límite)
    llm_max_dia_total: int = 3000  # mensajes a la IA de todos los usuarios en el día (0 = sin límite)
    exigir_aviso_privacidad: bool = True  # pedir autorización de tratamiento de datos (Ley 1581 de 2012)
    hora_recordatorio_defecto: str = "09:00"
    backup_dir: str = ""  # vacío = carpeta «backups» junto a la base de datos
    backups_conservar: int = 7

    @property
    def carpeta_respaldos(self) -> str:
        return self.backup_dir or os.path.join(os.path.dirname(os.path.abspath(self.db_path)), "backups")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @classmethod
    def desde_entorno(cls) -> "Config":
        token = os.getenv("TELEGRAM_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "Falta TELEGRAM_TOKEN. Copia .env.example a .env y pega el token de @BotFather."
            )
        return cls(
            telegram_token=token,
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip() or None,
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5").strip(),
            db_path=os.getenv("DB_PATH", "helassist.db").strip(),
            timezone=os.getenv("TIMEZONE", "America/Bogota").strip(),
            whisper_model=os.getenv("WHISPER_MODEL", "base").strip(),
            allowed_users=_enteros(os.getenv("ALLOWED_USERS", "")),
            llm_max_por_minuto=int(os.getenv("LLM_MAX_POR_MINUTO", "12")),
            llm_max_por_dia=int(os.getenv("LLM_MAX_POR_DIA", "40") or 0),
            llm_max_dia_total=int(os.getenv("LLM_MAX_DIA_TOTAL", "3000") or 0),
            exigir_aviso_privacidad=os.getenv("EXIGIR_AVISO_PRIVACIDAD", "si").strip().lower()
            not in ("no", "false", "0"),
            hora_recordatorio_defecto=os.getenv("HORA_RECORDATORIO_DEFECTO", "09:00").strip(),
            backup_dir=os.getenv("BACKUP_DIR", "").strip(),
            backups_conservar=int(os.getenv("BACKUPS_CONSERVAR", "7") or 7),
        )
