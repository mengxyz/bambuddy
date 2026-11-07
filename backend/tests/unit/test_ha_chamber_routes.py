from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from backend.app.api.routes.ha_chamber_heaters import get_binding
from backend.app.core.printer_scope import PrinterScope
from backend.app.models.external_chamber_binding import ExternalChamberBinding


@pytest.mark.asyncio
async def test_binding_outside_printer_scope_is_hidden():
    binding = ExternalChamberBinding(
        id=7,
        printer_id=2,
        name="Chamber",
        climate_entity_id="climate.chamber",
        duration_entity_id="number.chamber_duration",
        heater_ready_entity_id="binary_sensor.heater_ready",
        ventilation_ready_entity_id="binary_sensor.ventilation_ready",
    )
    db = AsyncMock()
    db.get.return_value = binding

    with pytest.raises(HTTPException) as exc:
        await get_binding(
            binding_id=binding.id,
            db=db,
            _=None,
            printer_scope=PrinterScope(frozenset({1})),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "External chamber binding not found"
