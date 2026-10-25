"""Built-in filament list endpoint coverage."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
@pytest.mark.integration
async def test_builtin_filaments_include_slot_temperature_ranges(async_client: AsyncClient):
    response = await async_client.get("/api/v1/cloud/builtin-filaments")

    assert response.status_code == 200
    filaments = {row["filament_id"]: row for row in response.json()}
    assert filaments["GFB00"] == {
        "filament_id": "GFB00",
        "name": "Bambu ABS",
        "nozzle_temp_min": 240,
        "nozzle_temp_max": 280,
    }
    assert filaments["GFG00"] == {
        "filament_id": "GFG00",
        "name": "Bambu PETG Basic",
        "nozzle_temp_min": 220,
        "nozzle_temp_max": 260,
    }
