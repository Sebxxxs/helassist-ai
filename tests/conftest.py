import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helassist.config import Config  # noqa: E402
from helassist.db import Database  # noqa: E402
from helassist.servicio import Servicio  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    return Config(telegram_token="123456:TEST", db_path=str(tmp_path / "t.db"))


@pytest.fixture
def db(cfg):
    return Database(cfg.db_path)


@pytest.fixture
def servicio(db, cfg):
    return Servicio(db, cfg)
