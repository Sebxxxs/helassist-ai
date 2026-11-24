"""Transcripción de notas de voz con Whisper (opcional).

Requiere: pip install openai-whisper   y   ffmpeg instalado en el sistema.
La transcripción corre en un hilo aparte para no congelar el bot.
"""
from __future__ import annotations

import asyncio
import logging
import shutil

log = logging.getLogger(__name__)
_modelo = None
_lock = asyncio.Lock()


def disponible() -> bool:
    try:
        import whisper  # noqa: F401
    except ImportError:
        return False
    return shutil.which("ffmpeg") is not None


def _cargar(nombre: str):
    global _modelo
    if _modelo is None:
        import whisper

        log.info("Cargando modelo Whisper '%s'...", nombre)
        _modelo = whisper.load_model(nombre)
    return _modelo


def _transcribir_sync(ruta: str, nombre_modelo: str) -> str:
    modelo = _cargar(nombre_modelo)
    resultado = modelo.transcribe(ruta, language="es", fp16=False)
    return resultado["text"].strip()


async def transcribir(ruta: str, nombre_modelo: str = "base") -> str:
    async with _lock:  # un audio a la vez: Whisper consume mucha CPU/RAM
        return await asyncio.to_thread(_transcribir_sync, ruta, nombre_modelo)
