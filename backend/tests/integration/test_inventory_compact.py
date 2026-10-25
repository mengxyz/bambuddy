"""Compact spool inventory endpoint coverage."""

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.printer_manager import printer_manager


async def _create_spool(db_session: AsyncSession, **overrides) -> Spool:
    values = {
        "material": "PLA",
        "subtype": "Silk",
        "brand": "Bambu",
        "color_name": "Gray",
        "rgba": "808080FF",
        "extra_colors": "ffffff,ff00ff,0000ff",
        "label_weight": 1000,
        "core_weight": 250,
        "weight_used": 250,
    }
    values.update(overrides)
    spool = Spool(**values)
    db_session.add(spool)
    await db_session.commit()
    await db_session.refresh(spool)
    return spool


@pytest.mark.asyncio
@pytest.mark.integration
async def test_compact_spools_returns_only_display_fields(async_client: AsyncClient, db_session: AsyncSession):
    spool = await _create_spool(db_session)

    response = await async_client.get("/api/v1/inventory/spools/compact")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": spool.id,
            "material": "PLA",
            "subtype": "Silk",
            "rgba": "808080FF",
            "extra_colors": "ffffff,ff00ff,0000ff",
            "brand": "Bambu",
            "available_weight": 750,
            "available_percent": 75,
            "assigned": False,
            "loaded": False,
            "printing": False,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_compact_spools_clamps_remaining_values_and_excludes_archived(
    async_client: AsyncClient, db_session: AsyncSession
):
    await _create_spool(db_session, material="ABS", label_weight=1000, weight_used=1200)
    await _create_spool(db_session, material="PETG", label_weight=0, weight_used=0)
    await _create_spool(db_session, material="TPU", archived_at=datetime(2026, 10, 25, tzinfo=timezone.utc))

    response = await async_client.get("/api/v1/inventory/spools/compact")

    assert response.status_code == 200
    rows = {row["material"]: row for row in response.json()}
    assert set(rows) == {"ABS", "PETG"}
    assert rows["ABS"]["available_weight"] == 0
    assert rows["ABS"]["available_percent"] == 0
    assert rows["PETG"]["available_weight"] == 0
    assert rows["PETG"]["available_percent"] == 0

    archived_response = await async_client.get("/api/v1/inventory/spools/compact?include_archived=true")
    assert {row["material"] for row in archived_response.json()} == {"ABS", "PETG", "TPU"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_compact_spools_reports_assignment_loaded_and_printing_state(
    async_client: AsyncClient,
    db_session: AsyncSession,
    printer_factory,
    monkeypatch: pytest.MonkeyPatch,
):
    spool = await _create_spool(db_session)
    printer = await printer_factory(model="P1S")
    db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=1, tray_id=2))
    await db_session.commit()

    status = PrinterState(connected=True, state="RUNNING", tray_now=6)
    monkeypatch.setattr(printer_manager, "get_all_statuses", lambda: {printer.id: status})

    response = await async_client.get("/api/v1/inventory/spools/compact")

    assert response.status_code == 200
    row = response.json()[0]
    assert row["assigned"] is True
    assert row["loaded"] is True
    assert row["printing"] is True

    idle_spool = await _create_spool(db_session, material="PETG")

    active_response = await async_client.get("/api/v1/inventory/spools/compact?assigned=true&loaded=true&printing=true")
    assert [row["id"] for row in active_response.json()] == [spool.id]

    available_response = await async_client.get("/api/v1/inventory/spools/compact?assigned=false")
    assert [row["id"] for row in available_response.json()] == [idle_spool.id]

    not_printing_response = await async_client.get("/api/v1/inventory/spools/compact?printing=false")
    assert [row["id"] for row in not_printing_response.json()] == [idle_spool.id]
