from backend.app.services.bambu_mqtt import BambuMQTTClient, PrinterState
from backend.app.services.printer_manager import printer_state_to_dict


def _client() -> BambuMQTTClient:
    return BambuMQTTClient(
        ip_address="192.168.1.10",
        serial_number="P1S-FILAMENT-SENSOR",
        access_code="12345678",
        model="P1S",
    )


def test_p1s_hw_switch_state_tracks_filament_presence_across_incremental_frames():
    client = _client()

    client._update_state({"hw_switch_state": 1})
    client._update_state({"mc_percent": 25})

    assert client.state.extruder_filament_sensor_reported is True
    assert client.state.extruder_filament_sensor_raw == 1
    assert client.state.filament_at_extruder is True

    client._update_state({"hw_switch_state": "0"})

    assert client.state.extruder_filament_sensor_raw == 0
    assert client.state.filament_at_extruder is False


def test_unreported_and_unknown_sensor_values_are_not_treated_as_empty():
    client = _client()

    assert client.state.extruder_filament_sensor_reported is False
    assert client.state.filament_at_extruder is None

    client._update_state({"hw_switch_state": 2})

    assert client.state.extruder_filament_sensor_reported is True
    assert client.state.extruder_filament_sensor_raw == 2
    assert client.state.filament_at_extruder is None


def test_filament_sensor_status_is_unknown_while_printer_is_offline():
    state = PrinterState(
        connected=False,
        extruder_filament_sensor_reported=True,
        filament_at_extruder=True,
        extruder_filament_sensor_raw=1,
    )

    status = printer_state_to_dict(state, model="P1S")

    assert status["extruder_filament_sensor_reported"] is True
    assert status["filament_at_extruder"] is None
    assert status["extruder_filament_sensor_raw"] == 1
