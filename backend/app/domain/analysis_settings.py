"""Analysis settings (D11, D20, D21, D24): global, versioned, stored under state_dir.

A value of None means "not set": the detector uses the default and, for the assumptions the
data does not decide, lowers the confidence of the plans the setting affects (D20).
"""

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.domain.errors import DomainError
from app.domain.sandbox import LOCK, atomic_write_json, retry_on_windows_lock
from app.domain.util import parse_date

# key -> (default, valid values). as_of's default None means "today".
SPECS: dict[str, tuple[Any, list[Any] | str]] = {
    "as_of": (None, "date, not later than today"),
    "data_up_to_date": (False, [True, False]),
    "period_calendar": ("anchored", ["anchored", "calendar"]),
    "amendment_proration": ("prorate", ["prorate", "full_period"]),
    "auto_renewal": (False, [True, False]),
}
# The assumptions the data does not decide: they lower confidence while unset (D20).
ASSUMPTION_KEYS = ("period_calendar", "amendment_proration", "auto_renewal")


@dataclass(frozen=True)
class AnalysisSettings:
    values: dict[str, Any] = field(default_factory=lambda: dict.fromkeys(SPECS))
    version: int = 0

    def effective(self, key: str) -> Any:
        v = self.values.get(key)
        return SPECS[key][0] if v is None else v

    def is_fixed(self, key: str) -> bool:
        return self.values.get(key) is not None

    def as_of(self, today: date) -> date:
        v = self.values.get("as_of")
        return parse_date(v, "as_of") if v else today

    def replace(self, **changes: Any) -> "AnalysisSettings":
        return AnalysisSettings({**self.values, **changes}, self.version)

    def to_dict(self) -> dict:
        return {"version": self.version, "values": dict(self.values)}


def validate_value(key: str, value: Any, today: date) -> Any:
    if key not in SPECS:
        raise DomainError(f"unknown setting '{key}'; valid: {', '.join(SPECS)}")
    if value is None:
        return None
    if key == "as_of":
        d = parse_date(value, "as_of")
        if d > today:
            raise DomainError(f"as_of {d.isoformat()} is later than today ({today.isoformat()})")
        return d.isoformat()
    valid = SPECS[key][1]
    # `1 in [True, False]` is true in Python: compare the type too.
    if not any(value == v and type(value) is type(v) for v in valid):
        raise DomainError(f"{key}: '{value}' is not valid; valid: {valid}")
    return value


class SettingsStore:
    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "analysis_settings.json"

    def get(self) -> AnalysisSettings:
        if not self.path.exists():
            return AnalysisSettings()
        raw = retry_on_windows_lock(lambda: json.loads(self.path.read_text(encoding="utf-8")))
        return AnalysisSettings({**dict.fromkeys(SPECS), **raw["values"]}, raw["version"])

    def update(self, changes: dict[str, Any], today: date) -> AnalysisSettings:
        """Validate every change first; nothing is written if one is invalid. Bumps the version."""
        validated = {k: validate_value(k, v, today) for k, v in changes.items()}
        with LOCK:
            current = self.get()
            new = AnalysisSettings({**current.values, **validated}, current.version + 1)
            atomic_write_json(self.path, new.to_dict())
            return new
