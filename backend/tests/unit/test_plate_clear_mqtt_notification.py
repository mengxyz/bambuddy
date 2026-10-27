"""Tests for the plate-clear gate reaching MQTT and notifications (#2525).

``awaiting_plate_clear`` is a Bambuddy-side flag (#961) — the printer's own MQTT
push only ever reports RUNNING/PAUSE/FAILED/FINISH/IDLE, so an external
automation had no way to tell "finished" from "finished and still waiting for
someone to clear the bed". It now rides along on the retained per-printer status
topic, gets its own retained topic on every transition, and can raise a
notification on the rising edge.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.mqtt_relay import MQTTRelayService
from backend.app.services.printer_manager import PrinterManager


def _relay() -> MQTTRelayService:
    relay = MQTTRelayService()
    relay.enabled = True
    relay.connected = True
    relay.client = MagicMock()
    return relay


def _state() -> SimpleNamespace:
    return SimpleNamespace(
        connected=True,
        state="FINISH",
        stg_cur=-1,
        progress=100,
        remaining_time=0,
        layer_num=250,
        total_layers=250,
        current_print="benchy.gcode.3mf",
        subtask_name="benchy",
        gcode_file="benchy.gcode",
        temperatures={"nozzle": 40, "bed": 30},
        wifi_signal="-50dBm",
        chamber_light="off",
        speed_level=2,
        cooling_fan_speed=0,
        big_fan1_speed=0,
        big_fan2_speed=0,
        heatbreak_fan_speed=0,
        left_aux_fan_speed=None,
        exhaust_fan_present=False,
    )


def _published(relay: MQTTRelayService) -> list[tuple[str, dict, bool]]:
    """Decode every publish as (topic, payload, retain)."""
    import json

    calls = []
    for call in relay.client.publish.call_args_list:
        topic = call.args[0]
        payload = json.loads(call.args[1])
        calls.append((topic, payload, call.kwargs.get("retain", False)))
    return calls


class TestStatusPayload:
    @pytest.mark.asyncio
    async def test_status_payload_carries_awaiting_plate_clear(self):
        relay = _relay()

        await relay.on_printer_status(1, _state(), "X1C", "01P00A000000001", True)

        topic, payload, retain = _published(relay)[0]
        assert topic == "bambuddy/printers/01P00A000000001/status"
        assert payload["awaiting_plate_clear"] is True
        assert retain is True

    @pytest.mark.asyncio
    async def test_status_payload_defaults_to_not_awaiting(self):
        relay = _relay()

        await relay.on_printer_status(1, _state(), "X1C", "01P00A000000001")

        _, payload, _ = _published(relay)[0]
        assert payload["awaiting_plate_clear"] is False
        # The pre-existing telemetry fields must survive the addition.
        assert payload["state"] == "FINISH"
        assert payload["progress"] == 100
        assert payload["speed_level"] == 2

    @pytest.mark.asyncio
    async def test_status_payload_carries_model_aware_stage_name(self):
        relay = _relay()
        state = _state()
        state.state = "RUNNING"
        state.stg_cur = 1

        await relay.on_printer_status(
            3,
            state,
            "Workshop P1S",
            "01S00C000000003",
            printer_model="P1S",
        )

        _, payload, _ = _published(relay)[0]
        assert payload["stg_cur_name"] == "Auto bed leveling"
        assert payload["speed_level"] == 2

    @pytest.mark.asyncio
    async def test_status_payload_suppresses_p1s_idle_stage_zero(self):
        relay = _relay()
        state = _state()
        state.state = "IDLE"
        state.stg_cur = 0

        await relay.on_printer_status(
            3,
            state,
            "Workshop P1S",
            "01S00C000000003",
            printer_model="P1S",
        )

        _, payload, _ = _published(relay)[0]
        assert payload["stg_cur_name"] is None

    @pytest.mark.asyncio
    async def test_status_payload_carries_normalized_ams_and_extruder_state(self):
        relay = _relay()
        state = _state()
        state.tray_now = 2
        state.ams_status_main = 1
        state.ams_status_sub = 3
        state.ams_filament_backup = True
        state.raw_data = {
            "ams": [
                {
                    "id": "0",
                    "temp": "28.5",
                    "humidity_raw": "42",
                    "tray": [
                        {
                            "id": "2",
                            "exists": True,
                            "tray_type": "PETG",
                            "tray_sub_brands": "PETG HF",
                            "tray_id_name": "Black PETG HF",
                            "tray_color": "161616FF",
                            "remain": 73,
                        }
                    ],
                }
            ],
            "vt_tray": {"id": 254, "tray_type": "PLA", "tray_color": "FFFFFFFF", "remain": 55},
        }
        state.active_extruder = 0
        state.extruder_filament_sensor_reported = True
        state.filament_at_extruder = True
        state.extruder_filament_sensor_raw = 1
        state.extruder_slots = {
            0: SimpleNamespace(ams_id=0, slot_id=2, has_filament=True),
            1: SimpleNamespace(ams_id=None, slot_id=None, has_filament=False),
        }

        await relay.on_printer_status(3, state, "P1S", "01S00C000000003")

        _, payload, retain = _published(relay)[0]
        assert retain is True
        assert payload["ams"]["tray_now"] == 2
        assert payload["ams"]["status_main"] == 1
        assert payload["ams"]["filament_backup"] is True
        assert payload["ams"]["units"][0]["humidity_percent"] == 42
        tray = payload["ams"]["units"][0]["trays"][0]
        assert tray == {
            "id": 2,
            "global_id": 2,
            "exists": True,
            "active": True,
            "type": "PETG",
            "sub_brand": "PETG HF",
            "name": "Black PETG HF",
            "color": "161616FF",
            "remaining_percent": 73,
        }
        assert payload["ams"]["external_trays"][0]["id"] == 254
        assert payload["extruder"] == {
            "active": 0,
            "filament_present": True,
            "slots": {
                "0": {"ams_id": 0, "tray_id": 2, "filament_present": True},
                "1": {"ams_id": None, "tray_id": None, "filament_present": False},
            },
        }


class TestPlateClearTopic:
    @pytest.mark.asyncio
    async def test_publishes_retained_state_on_its_own_topic(self):
        relay = _relay()

        await relay.on_plate_clear_state(3, "P1S", "01S00C000000003", True)

        topic, payload, retain = _published(relay)[0]
        assert topic == "bambuddy/printers/01S00C000000003/plate_clear"
        assert payload["awaiting"] is True
        assert payload["printer_id"] == 3
        assert payload["printer_name"] == "P1S"
        assert payload["printer_serial"] == "01S00C000000003"
        # Retained so a subscriber that connects later learns the current state
        # instead of waiting for the next transition.
        assert retain is True

    @pytest.mark.asyncio
    async def test_honours_the_configured_topic_prefix(self):
        relay = _relay()
        relay.topic_prefix = "farm/bambuddy"

        await relay.on_plate_clear_state(3, "P1S", "01S00C000000003", False)

        topic, payload, _ = _published(relay)[0]
        assert topic == "farm/bambuddy/printers/01S00C000000003/plate_clear"
        assert payload["awaiting"] is False

    @pytest.mark.asyncio
    async def test_silent_when_relay_is_disabled(self):
        relay = _relay()
        relay.enabled = False

        await relay.on_plate_clear_state(3, "P1S", "01S00C000000003", True)

        relay.client.publish.assert_not_called()


class TestEdgeTriggering:
    """The setter is re-asserted routinely (the queue clears the gate on every
    dispatch), so outward-facing emissions must fire on transitions only."""

    def _manager(self) -> PrinterManager:
        manager = PrinterManager()
        loop = MagicMock()
        loop.is_running.return_value = True
        manager._loop = loop
        return manager

    def test_emits_on_the_rising_edge(self):
        manager = self._manager()

        with patch.object(manager, "_schedule_async") as scheduled:
            manager.set_awaiting_plate_clear(7, True)

        emitted = [c for c in scheduled.call_args_list if "_emit_plate_clear_change" in repr(c.args[0])]
        assert len(emitted) == 1
        for call in scheduled.call_args_list:
            call.args[0].close()

    def test_does_not_re_emit_when_already_awaiting(self):
        manager = self._manager()
        manager._awaiting_plate_clear.add(7)

        with patch.object(manager, "_schedule_async") as scheduled:
            manager.set_awaiting_plate_clear(7, True)

        emitted = [c for c in scheduled.call_args_list if "_emit_plate_clear_change" in repr(c.args[0])]
        assert emitted == []
        # Persistence and the WebSocket broadcast are idempotent and stay unconditional.
        assert len(scheduled.call_args_list) == 2
        for call in scheduled.call_args_list:
            call.args[0].close()

    def test_does_not_emit_when_clearing_a_gate_that_was_never_up(self):
        manager = self._manager()

        with patch.object(manager, "_schedule_async") as scheduled:
            manager.set_awaiting_plate_clear(7, False)

        emitted = [c for c in scheduled.call_args_list if "_emit_plate_clear_change" in repr(c.args[0])]
        assert emitted == []
        for call in scheduled.call_args_list:
            call.args[0].close()

    def test_emits_on_the_falling_edge(self):
        manager = self._manager()
        manager._awaiting_plate_clear.add(7)

        with patch.object(manager, "_schedule_async") as scheduled:
            manager.set_awaiting_plate_clear(7, False)

        emitted = [c for c in scheduled.call_args_list if "_emit_plate_clear_change" in repr(c.args[0])]
        assert len(emitted) == 1
        for call in scheduled.call_args_list:
            call.args[0].close()


class TestEmitFanOut:
    @pytest.mark.asyncio
    async def test_rising_edge_publishes_and_notifies(self):
        manager = PrinterManager()
        manager._printer_info[7] = SimpleNamespace(name="X1C", serial_number="01P00A000000001")

        publish = AsyncMock()
        notify = AsyncMock()
        with (
            patch("backend.app.services.mqtt_relay.mqtt_relay.on_plate_clear_state", publish),
            patch(
                "backend.app.services.notification_service.notification_service.on_plate_clear_required",
                notify,
            ),
        ):
            await manager._emit_plate_clear_change(7, True)

        publish.assert_awaited_once_with(7, "X1C", "01P00A000000001", True)
        assert notify.await_count == 1
        assert notify.await_args.args[:2] == (7, "X1C")

    @pytest.mark.asyncio
    async def test_falling_edge_publishes_but_does_not_notify(self):
        manager = PrinterManager()
        manager._printer_info[7] = SimpleNamespace(name="X1C", serial_number="01P00A000000001")

        publish = AsyncMock()
        notify = AsyncMock()
        with (
            patch("backend.app.services.mqtt_relay.mqtt_relay.on_plate_clear_state", publish),
            patch(
                "backend.app.services.notification_service.notification_service.on_plate_clear_required",
                notify,
            ),
        ):
            await manager._emit_plate_clear_change(7, False)

        publish.assert_awaited_once_with(7, "X1C", "01P00A000000001", False)
        notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_printer_is_a_no_op(self):
        manager = PrinterManager()

        publish = AsyncMock()
        with patch("backend.app.services.mqtt_relay.mqtt_relay.on_plate_clear_state", publish):
            await manager._emit_plate_clear_change(999, True)

        publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_the_db_when_no_client_is_registered(self):
        """A printer disconnected outright has no cached info, but the gate can
        still be released through the API (#2864) — the retained topic must not
        be left asserting a state that is no longer true."""
        manager = PrinterManager()

        publish = AsyncMock()
        with (
            patch("backend.app.services.mqtt_relay.mqtt_relay.on_plate_clear_state", publish),
            patch.object(
                PrinterManager,
                "_printer_info_from_db",
                AsyncMock(return_value=SimpleNamespace(name="Powered-off X1C", serial_number="01P00A000000009")),
            ),
        ):
            await manager._emit_plate_clear_change(9, False)

        publish.assert_awaited_once_with(9, "Powered-off X1C", "01P00A000000009", False)

    @pytest.mark.asyncio
    async def test_mqtt_failure_does_not_block_the_notification(self):
        manager = PrinterManager()
        manager._printer_info[7] = SimpleNamespace(name="X1C", serial_number="01P00A000000001")

        notify = AsyncMock()
        with (
            patch(
                "backend.app.services.mqtt_relay.mqtt_relay.on_plate_clear_state",
                AsyncMock(side_effect=RuntimeError("broker down")),
            ),
            patch(
                "backend.app.services.notification_service.notification_service.on_plate_clear_required",
                notify,
            ),
        ):
            await manager._emit_plate_clear_change(7, True)

        assert notify.await_count == 1
