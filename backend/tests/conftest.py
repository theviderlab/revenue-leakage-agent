from datetime import date
from pathlib import Path

import pytest

from app.domain.billing import Billing

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DATA_THROUGH = date(2026, 6, 30)


@pytest.fixture
def billing(tmp_path):
    """Real data/ (read-only), a temp state dir and a fixed clock at data_through."""
    return Billing(DATA_DIR, tmp_path / "state", clock=lambda: DATA_THROUGH)
