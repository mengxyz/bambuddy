from unittest.mock import AsyncMock

import pytest

from backend.app.models.external_chamber_binding import ExternalChamberBinding
from backend.app.services.external_chamber import (
    ExternalChamberManager,
    parse_controller_state,
    validate_controller_contract,
)
from backend.app.services.homeassistant import homeassistant_service


def _climate():
    return {
        "entity_id": "climate.chamber",
        "state": "heat",
        "attributes": {
            "hvac_modes": ["off", "heat", "cool"],
            "current_temperature": 43,
            "temperature": 50,
            "hvac_action": "heating",
        },
    }


def _duration():
    return {
        "entity_id": "number.chamber_duration",
        "state": "15",
        "attributes": {"min": 15, "max": 1440, "step": 15},
    }


def _heater_ready(state="on"):
    return {
        "entity_id": "binary_sensor.chamber_heater_ready",
        "state": state,
        "attributes": {"friendly_name": "Heater Ready"},
    }


def _ventilation_ready(state="on"):
    return {
        "entity_id": "binary_sensor.chamber_ventilation_ready",
        "state": state,
        "attributes": {"friendly_name": "Ventilation Ready"},
    }


def _fan(state="on", percentage=70):
    return {
        "entity_id": "fan.chamber_ventilation",
        "state": state,
        "attributes": {"friendly_name": "Chamber Ventilation", "percentage": percentage},
    }


def _binding():
    return ExternalChamberBinding(
        id=1,
        printer_id=2,
        name="Chamber",
        climate_entity_id="climate.chamber",
        duration_entity_id="number.chamber_duration",
        heater_ready_entity_id="binary_sensor.chamber_heater_ready",
        ventilation_ready_entity_id="binary_sensor.chamber_ventilation_ready",
        ventilation_fan_entity_id="fan.chamber_ventilation",
        cooling_fan_percentage=70,
    )


def test_controller_contract_and_readiness_use_binary_sensor_state():
    assert validate_controller_contract(_climate(), _duration(), _heater_ready(), _ventilation_ready()) is None
    assert parse_controller_state(_climate(), _duration(), _heater_ready("off"), _ventilation_ready())["ready"] is False
    assert (
        parse_controller_state(_climate(), _duration(), _heater_ready("unavailable"), _ventilation_ready())["ready"]
        is False
    )
    assert parse_controller_state(_climate(), _duration(), _heater_ready("on"), _ventilation_ready())["ready"] is True


def test_controller_contract_rejects_incomplete_duration_range():
    duration = _duration()
    duration["attributes"]["max"] = 720
    assert "15–1440" in validate_controller_contract(_climate(), duration, _heater_ready(), _ventilation_ready())


def test_controller_contract_requires_heater_ready_binary_sensor():
    assert "Heater Ready" in validate_controller_contract(_climate(), _duration(), None, _ventilation_ready())


def test_controller_contract_requires_cool_and_ventilation_ready():
    climate = _climate()
    climate["attributes"]["hvac_modes"] = ["off", "heat"]
    assert "cool" in validate_controller_contract(climate, _duration(), _heater_ready(), _ventilation_ready())
    assert "Ventilation Ready" in validate_controller_contract(_climate(), _duration(), _heater_ready(), None)


def test_cool_readiness_uses_ventilation_sensor():
    climate = _climate()
    climate["state"] = "cool"

    status = parse_controller_state(climate, _duration(), _heater_ready("off"), _ventilation_ready("on"), _fan())

    assert status["ready"] is True
    assert status["heater_ready"] is False
    assert status["ventilation_ready"] is True


@pytest.mark.asyncio
async def test_heat_commands_run_in_safety_order(monkeypatch):
    manager = ExternalChamberManager()
    manager.refresh_binding = AsyncMock(return_value={"ready": True})
    calls = []

    async def set_duration(entity_id, value):
        calls.append(("duration", entity_id, value))
        return True

    async def set_target(entity_id, value):
        calls.append(("target", entity_id, value))
        return True

    async def set_mode(entity_id, value):
        calls.append(("mode", entity_id, value))
        return True

    monkeypatch.setattr(homeassistant_service, "set_number_value", set_duration)
    monkeypatch.setattr(homeassistant_service, "set_climate_temperature", set_target)
    monkeypatch.setattr(homeassistant_service, "set_climate_hvac_mode", set_mode)

    ok, error = await manager.command_heat(AsyncMock(), _binding(), 50, 15)

    assert ok is True
    assert error is None
    assert [call[0] for call in calls] == ["duration", "target", "mode"]


@pytest.mark.asyncio
async def test_heat_stops_after_rejected_duration(monkeypatch):
    manager = ExternalChamberManager()
    manager.refresh_binding = AsyncMock(return_value={"ready": True})
    set_duration = AsyncMock(return_value=False)
    set_target = AsyncMock(return_value=True)
    set_mode = AsyncMock(return_value=True)
    monkeypatch.setattr(homeassistant_service, "set_number_value", set_duration)
    monkeypatch.setattr(homeassistant_service, "set_climate_temperature", set_target)
    monkeypatch.setattr(homeassistant_service, "set_climate_hvac_mode", set_mode)

    ok, error = await manager.command_heat(AsyncMock(), _binding(), 50, 15)

    assert ok is False
    assert "duration" in error
    set_target.assert_not_awaited()
    set_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_cool_commands_set_fan_before_mode(monkeypatch):
    manager = ExternalChamberManager()
    manager.refresh_binding = AsyncMock(return_value={"ventilation_ready": True})
    calls = []

    async def record(kind, entity_id, value=None):
        calls.append((kind, entity_id, value))
        return True

    monkeypatch.setattr(
        homeassistant_service, "set_number_value", lambda entity_id, value: record("duration", entity_id, value)
    )
    monkeypatch.setattr(
        homeassistant_service, "set_climate_temperature", lambda entity_id, value: record("target", entity_id, value)
    )
    monkeypatch.setattr(
        homeassistant_service, "set_fan_percentage", lambda entity_id, value: record("fan_percentage", entity_id, value)
    )
    monkeypatch.setattr(homeassistant_service, "turn_on_fan", lambda entity_id: record("fan_on", entity_id))
    monkeypatch.setattr(
        homeassistant_service, "set_climate_hvac_mode", lambda entity_id, value: record("mode", entity_id, value)
    )

    ok, error = await manager.command_cool(AsyncMock(), _binding(), 35, 15)

    assert ok is True
    assert error is None
    assert [call[0] for call in calls] == ["duration", "target", "fan_percentage", "fan_on", "mode"]


@pytest.mark.asyncio
async def test_cool_mode_rejection_turns_fan_back_off(monkeypatch):
    manager = ExternalChamberManager()
    manager.refresh_binding = AsyncMock(return_value={"ventilation_ready": True})
    monkeypatch.setattr(homeassistant_service, "set_number_value", AsyncMock(return_value=True))
    monkeypatch.setattr(homeassistant_service, "set_climate_temperature", AsyncMock(return_value=True))
    monkeypatch.setattr(homeassistant_service, "set_fan_percentage", AsyncMock(return_value=True))
    monkeypatch.setattr(homeassistant_service, "turn_on_fan", AsyncMock(return_value=True))
    turn_off = AsyncMock(return_value=True)
    monkeypatch.setattr(homeassistant_service, "turn_off_fan", turn_off)
    monkeypatch.setattr(homeassistant_service, "set_climate_hvac_mode", AsyncMock(return_value=False))

    ok, error = await manager.command_cool(AsyncMock(), _binding(), 35, 15)

    assert ok is False
    assert "cool" in error
    turn_off.assert_awaited_once_with("fan.chamber_ventilation")
