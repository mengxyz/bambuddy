"""Linked inventory spool fields in compact printer MQTT status."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import backend.app.services.mqtt_relay as mqtt_relay_module
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.mqtt_relay import MQTTRelayService, _build_ams_relay_status


def test_linked_spool_fields_are_added_only_to_linked_trays():
    state = PrinterState(
        tray_now=1,
        raw_data={
            "ams": [
                {
                    "id": 0,
                    "tray": [
                        {"id": 0, "tray_type": "PLA", "remain": 80},
                        {"id": 1, "tray_type": "PETG", "remain": 50},
                    ],
                }
            ],
            "vt_tray": [{"id": 254, "tray_type": "TPU", "remain": 25}],
        },
    )
    linked = {
        1: {"spool_id": 12, "spool_weight": 500, "spool_weight_percent": 50},
        254: {"spool_id": 20, "spool_weight": 125, "spool_weight_percent": 25},
    }

    payload = _build_ams_relay_status(state, linked)

    unlinked_tray, linked_tray = payload["units"][0]["trays"]
    assert "spool_id" not in unlinked_tray
    assert "spool_weight" not in unlinked_tray
    assert "spool_weight_percent" not in unlinked_tray
    assert linked_tray["spool_id"] == 12
    assert linked_tray["spool_weight"] == 500
    assert linked_tray["spool_weight_percent"] == 50
    assert payload["external_trays"][0]["spool_id"] == 20


@pytest.mark.asyncio
async def test_internal_linked_spool_uses_inventory_remaining_weight(
    db_session: AsyncSession,
    test_engine,
    printer_factory,
    monkeypatch: pytest.MonkeyPatch,
):
    printer = await printer_factory(model="P1S")
    spool = Spool(
        material="PLA",
        subtype="Basic",
        brand="Bambu",
        rgba="FFFFFFFF",
        label_weight=1000,
        core_weight=250,
        weight_used=275.4,
    )
    db_session.add(spool)
    await db_session.flush()
    db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=0, tray_id=3))
    await db_session.commit()

    session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mqtt_relay_module, "async_session", session_maker)

    linked = await MQTTRelayService()._get_linked_spools(printer.id)

    assert linked == {
        3: {
            "spool_id": spool.id,
            "spool_weight": 725,
            "spool_weight_percent": 72,
        }
    }
