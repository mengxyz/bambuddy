"""Validation for the P1 preheat air-circulation setting."""

import pytest
from pydantic import ValidationError

from backend.app.schemas.settings import AppSettings, AppSettingsUpdate


def test_p1_preheat_aux_fan_defaults_off():
    assert AppSettings().preheat_p1_aux_fan_speed == 0


@pytest.mark.parametrize("speed", [0, 1, 40, 100])
def test_p1_preheat_aux_fan_accepts_percentage(speed: int):
    assert AppSettingsUpdate(preheat_p1_aux_fan_speed=speed).preheat_p1_aux_fan_speed == speed


@pytest.mark.parametrize("speed", [-1, 101])
def test_p1_preheat_aux_fan_rejects_out_of_range(speed: int):
    with pytest.raises(ValidationError):
        AppSettingsUpdate(preheat_p1_aux_fan_speed=speed)
