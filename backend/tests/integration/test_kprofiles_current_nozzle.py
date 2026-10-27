"""K-profile API nozzle-filter resolution tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


def _profile(slot_id: int, filament_id: str, extruder_id: int = 0):
    return SimpleNamespace(
        slot_id=slot_id,
        extruder_id=extruder_id,
        nozzle_id="",
        nozzle_diameter="0.4",
        filament_id=filament_id,
        name=f"Profile {filament_id}",
        k_value="0.02500",
        n_coef="1.000",
        ams_id=0,
        tray_id=-1,
        setting_id=f"setting-{slot_id}",
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_kprofiles_uses_current_printer_nozzle_when_filter_omitted(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(model="P1S")
    client = MagicMock()
    client.state = SimpleNamespace(
        connected=True,
        nozzles=[SimpleNamespace(nozzle_diameter="0.6")],
    )
    client.get_kprofiles = AsyncMock(return_value=[])

    with patch("backend.app.api.routes.kprofiles.printer_manager.get_client", return_value=client):
        response = await async_client.get(f"/api/v1/printers/{printer.id}/kprofiles/")

    assert response.status_code == 200
    assert response.json() == {"profiles": [], "nozzle_diameter": "0.6"}
    client.get_kprofiles.assert_awaited_once_with(nozzle_diameter="0.6")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_kprofiles_explicit_nozzle_filter_overrides_current_printer_nozzle(
    async_client: AsyncClient, printer_factory
):
    printer = await printer_factory(model="P1S")
    client = MagicMock()
    client.state = SimpleNamespace(
        connected=True,
        nozzles=[SimpleNamespace(nozzle_diameter="0.6")],
    )
    client.get_kprofiles = AsyncMock(return_value=[])

    with patch("backend.app.api.routes.kprofiles.printer_manager.get_client", return_value=client):
        response = await async_client.get(
            f"/api/v1/printers/{printer.id}/kprofiles/", params={"nozzle_diameter": "0.2"}
        )

    assert response.status_code == 200
    assert response.json() == {"profiles": [], "nozzle_diameter": "0.2"}
    client.get_kprofiles.assert_awaited_once_with(nozzle_diameter="0.2")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_kprofiles_filters_by_filament_id(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(model="P1S")
    client = MagicMock()
    client.state = SimpleNamespace(
        connected=True,
        nozzles=[SimpleNamespace(nozzle_diameter="0.4")],
    )
    client.get_kprofiles = AsyncMock(
        return_value=[
            _profile(slot_id=31, filament_id="GFG00"),
            _profile(slot_id=331, filament_id="GFG60"),
            _profile(slot_id=569, filament_id="GFG00"),
        ]
    )

    with patch("backend.app.api.routes.kprofiles.printer_manager.get_client", return_value=client):
        response = await async_client.get(f"/api/v1/printers/{printer.id}/kprofiles/", params={"filament_id": "GFG00"})

    assert response.status_code == 200
    data = response.json()
    assert data["nozzle_diameter"] == "0.4"
    assert [profile["slot_id"] for profile in data["profiles"]] == [31, 569]
    assert {profile["filament_id"] for profile in data["profiles"]} == {"GFG00"}
    client.get_kprofiles.assert_awaited_once_with(nozzle_diameter="0.4")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_kprofiles_filters_by_extruder_id(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(model="H2D")
    client = MagicMock()
    client.state = SimpleNamespace(
        connected=True,
        nozzles=[
            SimpleNamespace(nozzle_diameter="0.4"),
            SimpleNamespace(nozzle_diameter="0.4"),
        ],
    )
    client.get_kprofiles = AsyncMock(
        return_value=[
            _profile(slot_id=31, filament_id="GFG00", extruder_id=0),
            _profile(slot_id=32, filament_id="GFG00", extruder_id=1),
            _profile(slot_id=33, filament_id="GFG60", extruder_id=1),
        ]
    )

    with patch("backend.app.api.routes.kprofiles.printer_manager.get_client", return_value=client):
        response = await async_client.get(
            f"/api/v1/printers/{printer.id}/kprofiles/",
            params={"filament_id": "GFG00", "extruder_id": 1},
        )

    assert response.status_code == 200
    data = response.json()
    assert [profile["slot_id"] for profile in data["profiles"]] == [32]
    assert data["profiles"][0]["extruder_id"] == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_kprofiles_extruder_zero_is_not_treated_as_missing(async_client: AsyncClient, printer_factory):
    printer = await printer_factory(model="H2D")
    client = MagicMock()
    client.state = SimpleNamespace(
        connected=True,
        nozzles=[
            SimpleNamespace(nozzle_diameter="0.4"),
            SimpleNamespace(nozzle_diameter="0.4"),
        ],
    )
    client.get_kprofiles = AsyncMock(
        return_value=[
            _profile(slot_id=31, filament_id="GFG00", extruder_id=0),
            _profile(slot_id=32, filament_id="GFG00", extruder_id=1),
        ]
    )

    with patch("backend.app.api.routes.kprofiles.printer_manager.get_client", return_value=client):
        response = await async_client.get(f"/api/v1/printers/{printer.id}/kprofiles/", params={"extruder_id": 0})

    assert response.status_code == 200
    assert [profile["slot_id"] for profile in response.json()["profiles"]] == [31]
