"""Home Assistant external chamber-heater bindings."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import get_homeassistant_settings
from backend.app.core.auth import RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.external_chamber_binding import ExternalChamberBinding
from backend.app.models.printer import Printer
from backend.app.models.user import User
from backend.app.schemas.external_chamber_binding import (
    ExternalChamberBindingCreate,
    ExternalChamberBindingResponse,
    ExternalChamberBindingUpdate,
    ExternalChamberEntity,
    ExternalChamberTestRequest,
    ExternalChamberTestResponse,
)
from backend.app.services.external_chamber import (
    external_chamber_manager,
    parse_controller_state,
    validate_controller_contract,
)
from backend.app.services.homeassistant import homeassistant_service
from backend.app.services.printer_manager import supports_chamber_heater

router = APIRouter(prefix="/ha-chamber-heaters", tags=["ha-chamber-heaters"])

_READ = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_READ)
_CREATE = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_CREATE)
_UPDATE = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_UPDATE)
_DELETE = RequirePermissionIfAuthEnabled(Permission.SMART_PLUGS_DELETE)


async def _configure(db: AsyncSession) -> None:
    settings = await get_homeassistant_settings(db)
    if not settings["ha_url"] or not settings["ha_token"]:
        raise HTTPException(400, "Home Assistant is not configured")
    homeassistant_service.configure(settings["ha_url"], settings["ha_token"])


async def _validate_entities(
    db: AsyncSession,
    climate_id: str,
    duration_id: str,
    heater_ready_id: str,
    ventilation_ready_id: str,
    ventilation_fan_id: str | None = None,
) -> dict:
    await _configure(db)
    entity_ids = [climate_id, duration_id, heater_ready_id, ventilation_ready_id]
    if ventilation_fan_id:
        entity_ids.append(ventilation_fan_id)
    states = await homeassistant_service.fetch_states(entity_ids)
    error = validate_controller_contract(
        states.get(climate_id),
        states.get(duration_id),
        states.get(heater_ready_id),
        states.get(ventilation_ready_id),
        states.get(ventilation_fan_id) if ventilation_fan_id else None,
    )
    if error:
        raise HTTPException(400, error)
    return parse_controller_state(
        states[climate_id],
        states[duration_id],
        states[heater_ready_id],
        states[ventilation_ready_id],
        states.get(ventilation_fan_id) if ventilation_fan_id else None,
    )


@router.get("/", response_model=list[ExternalChamberBindingResponse])
async def list_bindings(
    printer_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _: User | None = _READ,
):
    query = select(ExternalChamberBinding).order_by(ExternalChamberBinding.name)
    if printer_id is not None:
        query = query.where(ExternalChamberBinding.printer_id == printer_id)
    return list((await db.scalars(query)).all())


@router.get("/entities", response_model=list[ExternalChamberEntity])
async def list_entities(
    search: str | None = Query(default=None, max_length=100),
    db: AsyncSession = Depends(get_db),
    _: User | None = _READ,
):
    settings = await get_homeassistant_settings(db)
    if not settings["ha_url"] or not settings["ha_token"]:
        raise HTTPException(400, "Home Assistant is not configured")
    entities = await homeassistant_service.list_chamber_controller_entities(settings["ha_url"], settings["ha_token"])
    if search:
        needle = search.strip().lower()
        entities = [
            entity
            for entity in entities
            if needle in entity["entity_id"].lower() or needle in entity["friendly_name"].lower()
        ]
    return entities


@router.post("/test", response_model=ExternalChamberTestResponse)
async def test_controller(
    data: ExternalChamberTestRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = _READ,
):
    try:
        status = await _validate_entities(
            db,
            data.climate_entity_id,
            data.duration_entity_id,
            data.heater_ready_entity_id,
            data.ventilation_ready_entity_id,
            data.ventilation_fan_entity_id,
        )
    except HTTPException as exc:
        return ExternalChamberTestResponse(success=False, error=str(exc.detail))
    return ExternalChamberTestResponse(success=True, status=status)


@router.post("/", response_model=ExternalChamberBindingResponse, status_code=201)
async def create_binding(
    data: ExternalChamberBindingCreate,
    db: AsyncSession = Depends(get_db),
    _: User | None = _CREATE,
):
    printer = await db.get(Printer, data.printer_id)
    if not printer:
        raise HTTPException(404, "Printer not found")
    if supports_chamber_heater(printer.model):
        raise HTTPException(400, "Printers with a native active chamber heater cannot use an external controller")
    await _validate_entities(
        db,
        data.climate_entity_id,
        data.duration_entity_id,
        data.heater_ready_entity_id,
        data.ventilation_ready_entity_id,
        data.ventilation_fan_entity_id,
    )
    clash = await db.scalar(
        select(ExternalChamberBinding).where(
            or_(
                ExternalChamberBinding.printer_id == data.printer_id,
                ExternalChamberBinding.climate_entity_id == data.climate_entity_id,
                ExternalChamberBinding.duration_entity_id == data.duration_entity_id,
                ExternalChamberBinding.heater_ready_entity_id == data.heater_ready_entity_id,
                ExternalChamberBinding.ventilation_ready_entity_id == data.ventilation_ready_entity_id,
                *(
                    [ExternalChamberBinding.ventilation_fan_entity_id == data.ventilation_fan_entity_id]
                    if data.ventilation_fan_entity_id
                    else []
                ),
            )
        )
    )
    if clash:
        raise HTTPException(409, "Printer or Home Assistant controller is already bound")
    binding = ExternalChamberBinding(**data.model_dump())
    db.add(binding)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Printer or Home Assistant controller is already bound") from exc
    await db.refresh(binding)
    await external_chamber_manager.refresh_binding(db, binding)
    return binding


@router.get("/{binding_id}", response_model=ExternalChamberBindingResponse)
async def get_binding(
    binding_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = _READ,
):
    binding = await db.get(ExternalChamberBinding, binding_id)
    if not binding:
        raise HTTPException(404, "External chamber binding not found")
    return binding


@router.patch("/{binding_id}", response_model=ExternalChamberBindingResponse)
async def update_binding(
    binding_id: int,
    data: ExternalChamberBindingUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = _UPDATE,
):
    binding = await db.get(ExternalChamberBinding, binding_id)
    if not binding:
        raise HTTPException(404, "External chamber binding not found")
    updates = data.model_dump(exclude_unset=True)
    entity_fields = {
        "climate_entity_id",
        "duration_entity_id",
        "heater_ready_entity_id",
        "ventilation_ready_entity_id",
        "ventilation_fan_entity_id",
    }
    if binding.owner_queue_item_id is not None and ("printer_id" in updates or entity_fields.intersection(updates)):
        raise HTTPException(409, "Printer and Home Assistant entities cannot be changed during an active session")
    if updates.get("enabled") is False and binding.enabled and binding.owner_queue_item_id is not None:
        if not await external_chamber_manager.command_off(db, binding):
            raise HTTPException(409, "Cannot disable the binding until its active session can be stopped safely")
    old_printer_id = binding.printer_id
    printer_id = updates.get("printer_id", binding.printer_id)
    if printer_id != binding.printer_id:
        printer = await db.get(Printer, printer_id)
        if not printer:
            raise HTTPException(404, "Printer not found")
        if supports_chamber_heater(printer.model):
            raise HTTPException(400, "Printers with a native active chamber heater cannot use an external controller")
        printer_clash = await db.scalar(
            select(ExternalChamberBinding).where(
                ExternalChamberBinding.id != binding_id,
                ExternalChamberBinding.printer_id == printer_id,
            )
        )
        if printer_clash:
            raise HTTPException(409, "Printer is already bound to a Home Assistant controller")
    climate_id = updates.get("climate_entity_id", binding.climate_entity_id)
    duration_id = updates.get("duration_entity_id", binding.duration_entity_id)
    heater_ready_id = updates.get("heater_ready_entity_id", binding.heater_ready_entity_id)
    ventilation_ready_id = updates.get("ventilation_ready_entity_id", binding.ventilation_ready_entity_id)
    ventilation_fan_id = updates.get("ventilation_fan_entity_id", binding.ventilation_fan_entity_id)
    if not heater_ready_id:
        raise HTTPException(400, "Heater Ready entity is required")
    if not ventilation_ready_id and entity_fields.intersection(updates):
        raise HTTPException(400, "Ventilation Ready entity is required for remapped controllers")
    if ventilation_ready_id and (
        climate_id != binding.climate_entity_id
        or duration_id != binding.duration_entity_id
        or heater_ready_id != binding.heater_ready_entity_id
        or ventilation_ready_id != binding.ventilation_ready_entity_id
        or ventilation_fan_id != binding.ventilation_fan_entity_id
    ):
        await _validate_entities(
            db,
            climate_id,
            duration_id,
            heater_ready_id,
            ventilation_ready_id,
            ventilation_fan_id,
        )
    clash_conditions = [
        ExternalChamberBinding.climate_entity_id == climate_id,
        ExternalChamberBinding.duration_entity_id == duration_id,
        ExternalChamberBinding.heater_ready_entity_id == heater_ready_id,
    ]
    if ventilation_ready_id:
        clash_conditions.append(ExternalChamberBinding.ventilation_ready_entity_id == ventilation_ready_id)
    if ventilation_fan_id:
        clash_conditions.append(ExternalChamberBinding.ventilation_fan_entity_id == ventilation_fan_id)
    clash = await db.scalar(
        select(ExternalChamberBinding).where(
            ExternalChamberBinding.id != binding_id,
            or_(*clash_conditions),
        )
    )
    if clash:
        raise HTTPException(409, "Home Assistant controller is already bound")
    for field, value in updates.items():
        setattr(binding, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Home Assistant controller is already bound") from exc
    await db.refresh(binding)
    if old_printer_id != binding.printer_id:
        external_chamber_manager.forget(old_printer_id)
    await external_chamber_manager.refresh_binding(db, binding)
    return binding


@router.delete("/{binding_id}")
async def delete_binding(
    binding_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = _DELETE,
):
    binding = await db.get(ExternalChamberBinding, binding_id)
    if not binding:
        raise HTTPException(404, "External chamber binding not found")
    printer_id = binding.printer_id
    if binding.owner_queue_item_id is not None:
        await external_chamber_manager.command_off(db, binding)
    await db.delete(binding)
    await db.commit()
    external_chamber_manager.forget(printer_id)
    return {"message": "External chamber binding removed"}
