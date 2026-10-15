"""Runtime cache and command lifecycle for Home Assistant chamber heaters."""

import asyncio
import inspect
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import get_homeassistant_settings
from backend.app.core.database import async_session
from backend.app.models.external_chamber_binding import ExternalChamberBinding
from backend.app.services.homeassistant import as_float, homeassistant_service
from backend.app.utils.local_time import utcnow_naive

logger = logging.getLogger(__name__)

LEASE_MINUTES = 15
LEASE_RENEW_SECONDS = 5 * 60
POLL_SECONDS = 10


def parse_controller_state(
    climate: dict | None,
    duration: dict | None,
    heater_ready: dict | None,
    ventilation_ready: dict | None = None,
    fan: dict | None = None,
    binding=None,
) -> dict:
    attrs = (climate or {}).get("attributes") or {}
    mode = str((climate or {}).get("state") or "unavailable").lower()
    current = as_float(attrs.get("current_temperature"))
    target = as_float(attrs.get("temperature"))
    duration_value = as_float((duration or {}).get("state"))
    duration_state = str((duration or {}).get("state") or "unavailable").lower()
    heater_readiness_state = str((heater_ready or {}).get("state") or "unavailable").lower()
    ventilation_readiness_state = str((ventilation_ready or {}).get("state") or "unavailable").lower()
    fan_state = str((fan or {}).get("state") or "unavailable").lower()
    fan_percentage = as_float(((fan or {}).get("attributes") or {}).get("percentage"))
    reachable = bool(
        climate
        and duration
        and heater_ready
        and mode not in {"unknown", "unavailable"}
        and duration_state not in {"unknown", "unavailable"}
    )
    # The controller contract exposes CH32 powerStatus exclusively through a
    # dedicated binary_sensor. Never infer readiness from climate availability,
    # temperature, output, or a similarly named climate attribute.
    heater_is_ready = reachable and heater_readiness_state == "on"
    ventilation_is_ready = bool(reachable and ventilation_ready and ventilation_readiness_state == "on")
    ready = (
        ventilation_is_ready
        if mode == "cool"
        else heater_is_ready
        if mode == "heat"
        else (heater_is_ready or ventilation_is_ready)
    )
    action = attrs.get("hvac_action") or attrs.get("action")
    remaining = as_float(attrs.get("remaining_min", attrs.get("remaining_minutes")))
    command_state = str(attrs.get("command_state") or "").lower()
    active_not_ready = (mode == "heat" and not heater_is_ready) or (mode == "cool" and not ventilation_is_ready)
    fault = active_not_ready or command_state in {"rejected", "failed"} or mode == "failsafe"
    return {
        "ready": ready,
        "heater_ready": heater_is_ready,
        "heater_ready_state": heater_readiness_state,
        "ventilation_ready": ventilation_is_ready,
        "ventilation_ready_state": ventilation_readiness_state,
        "reachable": reachable,
        "mode": mode,
        "action": action,
        "current_temperature": current,
        "target_temperature": target,
        "duration_minutes": int(duration_value) if duration_value is not None else None,
        "remaining_minutes": int(remaining) if remaining is not None else None,
        "fan_state": fan_state if fan else None,
        "fan_percentage": int(fan_percentage) if fan_percentage is not None else None,
        "fan_mapped": fan is not None or bool(getattr(binding, "ventilation_fan_entity_id", None)),
        "supports_heat": "heat" in set(attrs.get("hvac_modes") or []),
        "supports_cool": "cool" in set(attrs.get("hvac_modes") or []),
        "owned": bool(binding and binding.owner_queue_item_id is not None),
        "owner_queue_item_id": getattr(binding, "owner_queue_item_id", None),
        "owned_mode": getattr(binding, "owned_mode", None),
        "owned_fan_percentage": getattr(binding, "owned_fan_percentage", None),
        "lifecycle_state": getattr(binding, "lifecycle_state", "idle"),
        "fault": fault,
        "fault_notified": bool(getattr(binding, "fault_notified", False)),
        "command_state": command_state or None,
    }


def validate_controller_contract(
    climate: dict | None,
    duration: dict | None,
    heater_ready: dict | None,
    ventilation_ready: dict | None,
    fan: dict | None = None,
) -> str | None:
    if not climate or climate.get("entity_id", "").partition(".")[0] != "climate":
        return "Climate entity is unavailable"
    attrs = climate.get("attributes") or {}
    modes = set(attrs.get("hvac_modes") or [])
    if not {"heat", "cool", "off"}.issubset(modes):
        return "Climate entity must support heat, cool, and off modes"
    if as_float(attrs.get("current_temperature")) is None:
        return "Climate entity must expose current_temperature"
    if as_float(attrs.get("temperature")) is None:
        return "Climate entity must expose a target temperature"
    if not duration or duration.get("entity_id", "").partition(".")[0] != "number":
        return "Duration entity is unavailable"
    dattrs = duration.get("attributes") or {}
    minimum = as_float(dattrs.get("min"))
    maximum = as_float(dattrs.get("max"))
    step = as_float(dattrs.get("step"))
    if minimum is None or maximum is None or minimum > 15 or maximum < 1440 or step != 15:
        return "Duration entity must support the complete 15–1440 minute range"
    if not heater_ready or heater_ready.get("entity_id", "").partition(".")[0] != "binary_sensor":
        return "Heater Ready entity is unavailable"
    if not ventilation_ready or ventilation_ready.get("entity_id", "").partition(".")[0] != "binary_sensor":
        return "Ventilation Ready entity is unavailable"
    if fan:
        if fan.get("entity_id", "").partition(".")[0] != "fan":
            return "Ventilation Fan entity is unavailable"
        if "percentage" not in (fan.get("attributes") or {}):
            return "Ventilation Fan entity must support percentage control"
    return None


class ExternalChamberManager:
    def __init__(self):
        self._cache: dict[int, dict] = {}
        self._stopping = asyncio.Event()

    def get_status(self, printer_id: int) -> dict | None:
        value = self._cache.get(printer_id)
        return dict(value) if value else None

    def forget(self, printer_id: int) -> None:
        self._cache.pop(printer_id, None)

    async def configure(self, db: AsyncSession) -> bool:
        settings = await get_homeassistant_settings(db)
        homeassistant_service.configure(settings["ha_url"], settings["ha_token"])
        return bool(settings["ha_url"] and settings["ha_token"])

    async def refresh_binding(self, db: AsyncSession, binding: ExternalChamberBinding) -> dict:
        if not await self.configure(db):
            status = parse_controller_state(None, None, None, None, None, binding)
        else:
            entity_ids = [binding.climate_entity_id, binding.duration_entity_id, binding.heater_ready_entity_id]
            if binding.ventilation_ready_entity_id:
                entity_ids.append(binding.ventilation_ready_entity_id)
            if binding.ventilation_fan_entity_id:
                entity_ids.append(binding.ventilation_fan_entity_id)
            states = await homeassistant_service.fetch_states(entity_ids)
            status = parse_controller_state(
                states.get(binding.climate_entity_id),
                states.get(binding.duration_entity_id),
                states.get(binding.heater_ready_entity_id),
                states.get(binding.ventilation_ready_entity_id) if binding.ventilation_ready_entity_id else None,
                states.get(binding.ventilation_fan_entity_id) if binding.ventilation_fan_entity_id else None,
                binding,
            )
        status["binding_id"] = binding.id
        status["name"] = binding.name
        status["strict"] = binding.strict
        status["manual_duration_minutes"] = binding.manual_duration_minutes
        status["cooling_fan_percentage"] = binding.cooling_fan_percentage
        self._cache[binding.printer_id] = status
        return status

    async def refresh_printer(self, db: AsyncSession, printer_id: int) -> dict | None:
        binding = await db.scalar(
            select(ExternalChamberBinding).where(
                ExternalChamberBinding.printer_id == printer_id,
                ExternalChamberBinding.enabled.is_(True),
            )
        )
        if not binding:
            self.forget(printer_id)
            return None
        return await self.refresh_binding(db, binding)

    async def command_heat(
        self,
        db: AsyncSession,
        binding: ExternalChamberBinding,
        target: int,
        duration_minutes: int,
        *,
        queue_item_id: int | None = None,
    ) -> tuple[bool, str | None]:
        return await self.command_mode(
            db,
            binding,
            "heat",
            target,
            duration_minutes,
            queue_item_id=queue_item_id,
        )

    async def command_cool(
        self,
        db: AsyncSession,
        binding: ExternalChamberBinding,
        target: int,
        duration_minutes: int,
        *,
        queue_item_id: int | None = None,
    ) -> tuple[bool, str | None]:
        return await self.command_mode(
            db,
            binding,
            "cool",
            target,
            duration_minutes,
            queue_item_id=queue_item_id,
        )

    async def command_mode(
        self,
        db: AsyncSession,
        binding: ExternalChamberBinding,
        mode: str,
        target: int,
        duration_minutes: int,
        *,
        queue_item_id: int | None = None,
    ) -> tuple[bool, str | None]:
        if mode not in {"heat", "cool"}:
            return False, "External chamber mode must be heat or cool"
        minimum = 40 if mode == "heat" else 20
        if target < minimum or target > 60:
            return False, f"External chamber {mode} target must be between {minimum}°C and 60°C"
        if duration_minutes < 15 or duration_minutes > 1440:
            return False, "External chamber duration must be between 15 and 1440 minutes"
        status = await self.refresh_binding(db, binding)
        mode_ready = (
            status.get("heater_ready", status.get("ready", False))
            if mode == "heat"
            else status.get("ventilation_ready", False)
        )
        if not mode_ready:
            subsystem = "heater" if mode == "heat" else "ventilation"
            return False, f"External chamber {subsystem} is not ready"
        # Contract order is safety-significant: lease, target, optional fan, then mode.
        if not await homeassistant_service.set_number_value(binding.duration_entity_id, duration_minutes):
            return False, "External chamber controller rejected the duration command"
        if not await homeassistant_service.set_climate_temperature(binding.climate_entity_id, target):
            return False, "External chamber controller rejected the target command"
        fan_percentage = None
        if mode == "cool" and binding.ventilation_fan_entity_id:
            fan_percentage = binding.cooling_fan_percentage
            if not await homeassistant_service.set_fan_percentage(binding.ventilation_fan_entity_id, fan_percentage):
                return False, "External chamber controller rejected the fan percentage command"
            if fan_percentage > 0 and not await homeassistant_service.turn_on_fan(binding.ventilation_fan_entity_id):
                await homeassistant_service.turn_off_fan(binding.ventilation_fan_entity_id)
                return False, "External chamber controller rejected the fan command"
        if not await homeassistant_service.set_climate_hvac_mode(binding.climate_entity_id, mode):
            if mode == "cool" and binding.ventilation_fan_entity_id:
                await homeassistant_service.turn_off_fan(binding.ventilation_fan_entity_id)
            return False, f"External chamber controller rejected the {mode} command"
        if queue_item_id is not None:
            binding.owner_queue_item_id = queue_item_id
            binding.owned_target = target
            binding.owned_duration_minutes = duration_minutes
            binding.owned_mode = mode
            binding.owned_fan_percentage = fan_percentage
            binding.lifecycle_state = "heating" if mode == "heat" else "cooling"
            binding.lease_renewed_at = utcnow_naive()
            binding.fault_notified = False
            await db.commit()
        await self.refresh_binding(db, binding)
        return True, None

    async def command_off(self, db: AsyncSession, binding: ExternalChamberBinding, *, force: bool = False) -> bool:
        status = await self.refresh_binding(db, binding)
        if not force and binding.owner_queue_item_id is not None:
            if not status["reachable"]:
                # Keep the persisted owner so a later poll can compare-and-stop
                # once HA is reachable again.
                return False
            target_matches = status["target_temperature"] is not None and int(status["target_temperature"]) == int(
                binding.owned_target or -1
            )
            owned_mode = binding.owned_mode or "heat"
            fan_matches = True
            if owned_mode == "cool" and binding.ventilation_fan_entity_id:
                fan_matches = status.get("fan_percentage") == binding.owned_fan_percentage
            if status["mode"] != owned_mode or not target_matches or not fan_matches:
                await self.relinquish(db, binding)
                return False
        ok = await homeassistant_service.set_climate_hvac_mode(binding.climate_entity_id, "off")
        if ok and binding.ventilation_fan_entity_id and (force or (binding.owned_mode or "heat") == "cool"):
            fan_ok = await homeassistant_service.turn_off_fan(binding.ventilation_fan_entity_id)
            if not fan_ok and binding.owner_queue_item_id is not None:
                binding.lifecycle_state = "stopping"
                await db.commit()
            ok = fan_ok
        if ok and binding.owner_queue_item_id is not None:
            await self.relinquish(db, binding)
        await self.refresh_binding(db, binding)
        return ok

    async def relinquish(self, db: AsyncSession, binding: ExternalChamberBinding) -> None:
        binding.owner_queue_item_id = None
        binding.owned_target = None
        binding.owned_duration_minutes = None
        binding.owned_mode = None
        binding.owned_fan_percentage = None
        binding.lifecycle_state = "idle"
        binding.lease_renewed_at = None
        binding.fault_notified = False
        await db.commit()

    async def mark_print_started(self, db: AsyncSession, queue_item_id: int) -> None:
        binding = await db.scalar(
            select(ExternalChamberBinding).where(ExternalChamberBinding.owner_queue_item_id == queue_item_id)
        )
        if binding:
            binding.lifecycle_state = "running"
            await db.commit()

    async def cleanup_aborted_dispatch(self, db: AsyncSession, queue_item_id: int) -> None:
        pending = db.scalar(
            select(ExternalChamberBinding).where(ExternalChamberBinding.owner_queue_item_id == queue_item_id)
        )
        candidate = await pending if inspect.isawaitable(pending) else pending
        binding = candidate if isinstance(candidate, ExternalChamberBinding) else None
        if binding and binding.lifecycle_state != "running":
            await self.command_off(db, binding)

    def overlay(self, printer_id: int, payload: dict) -> dict:
        external = self.get_status(printer_id)
        if not external:
            payload.setdefault("chamber_heater_source", "native" if payload.get("supports_chamber_heater") else None)
            return payload
        temps = dict(payload.get("temperatures") or {})
        if external["current_temperature"] is not None:
            temps["chamber"] = external["current_temperature"]
        if external["target_temperature"] is not None:
            temps["chamber_target"] = external["target_temperature"]
        temps["chamber_heating"] = external["mode"] == "heat" and external["action"] in {"heating", "heat"}
        temps["chamber_cooling"] = external["mode"] == "cool" and external["action"] in {"cooling", "cool"}
        payload["temperatures"] = temps
        payload["supports_chamber_heater"] = True
        payload["chamber_heater_source"] = "home_assistant"
        payload["external_chamber"] = external
        return payload

    async def run(self) -> None:
        self._stopping.clear()
        recovering = True
        while not self._stopping.is_set():
            try:
                async with async_session() as db:
                    bindings = list(
                        (
                            await db.scalars(
                                select(ExternalChamberBinding).where(ExternalChamberBinding.enabled.is_(True))
                            )
                        ).all()
                    )
                    for binding in bindings:
                        status = await self.refresh_binding(db, binding)
                        if binding.owner_queue_item_id is None:
                            continue

                        if binding.lifecycle_state == "stopping" and status["mode"] == "off":
                            fan_stopped = (
                                not binding.ventilation_fan_entity_id
                                or await homeassistant_service.turn_off_fan(binding.ventilation_fan_entity_id)
                            )
                            if fan_stopped:
                                await self.relinquish(db, binding)
                            continue

                        # A local/HA target or mode change is manual takeover.
                        # Relinquish without sending anything back.
                        target_matches = status["target_temperature"] is not None and int(
                            status["target_temperature"]
                        ) == int(binding.owned_target or -1)
                        owned_mode = binding.owned_mode or "heat"
                        fan_matches = True
                        if owned_mode == "cool" and binding.ventilation_fan_entity_id:
                            fan_matches = status.get("fan_percentage") == binding.owned_fan_percentage
                        if (
                            status["mode"] not in {owned_mode, "unavailable"}
                            or not fan_matches
                            or (status["target_temperature"] is not None and not target_matches)
                        ):
                            logger.info("External chamber ownership relinquished for printer %s", binding.printer_id)
                            await self.relinquish(db, binding)
                            continue

                        from backend.app.models.print_queue import PrintQueueItem
                        from backend.app.services.printer_manager import printer_manager

                        item = await db.get(PrintQueueItem, binding.owner_queue_item_id)
                        printer_state = printer_manager.get_status(binding.printer_id)
                        active_print = bool(
                            printer_state and printer_state.state in {"PREPARE", "SLICING", "RUNNING", "PAUSE"}
                        )
                        if item is None or item.status in {"completed", "failed", "cancelled"}:
                            await self.command_off(db, binding)
                            continue

                        # A process restart cannot resume an interrupted preheat
                        # coroutine. Reclaim only rows that had already crossed
                        # the durable pending -> printing boundary; stale pending
                        # ownership is compare-and-stopped instead.
                        if recovering and item.status != "printing":
                            binding.lifecycle_state = "stopping"
                            await db.commit()
                            await self.command_off(db, binding)
                            continue

                        if binding.lifecycle_state == "stopping":
                            await self.command_off(db, binding)
                            continue

                        mode_ready = (
                            status.get("heater_ready", False)
                            if owned_mode == "heat"
                            else status.get("ventilation_ready", False)
                        )
                        if not mode_ready and active_print:
                            binding.lifecycle_state = "fault"
                            if not binding.fault_notified:
                                client = printer_manager.get_client(binding.printer_id)
                                if client and printer_state and printer_state.state != "PAUSE":
                                    client.pause_print()
                                binding.fault_notified = True
                                await db.commit()
                                from backend.app.services.notification_service import notification_service

                                printer_info = printer_manager.get_printer(binding.printer_id)
                                await notification_service.on_external_chamber_fault(
                                    binding.printer_id,
                                    printer_info.name if printer_info else f"Printer {binding.printer_id}",
                                    binding.name,
                                    db,
                                )
                                logger.error(
                                    "External chamber fault on printer %s; printer paused for inspection",
                                    binding.printer_id,
                                )
                            continue

                        if mode_ready and binding.lifecycle_state == "fault":
                            # Restore the owned climate mode, but deliberately never auto-resume.
                            fault_was_notified = binding.fault_notified
                            ok, _ = await self.command_mode(
                                db,
                                binding,
                                owned_mode,
                                int(binding.owned_target or 0),
                                LEASE_MINUTES,
                                queue_item_id=binding.owner_queue_item_id,
                            )
                            if ok:
                                binding.lifecycle_state = "paused_inspection"
                                binding.fault_notified = fault_was_notified
                                await db.commit()

                        renewed = binding.lease_renewed_at
                        renewal_due = (
                            renewed is None or (utcnow_naive() - renewed).total_seconds() >= LEASE_RENEW_SECONDS
                        )
                        workflow_active = (
                            active_print
                            or item.status == "printing"
                            or binding.lifecycle_state
                            in {
                                "heating",
                                "cooling",
                            }
                        )
                        if mode_ready and workflow_active and renewal_due:
                            ok = await homeassistant_service.set_number_value(binding.duration_entity_id, LEASE_MINUTES)
                            if ok:
                                binding.lease_renewed_at = utcnow_naive()
                                printer_state_name = getattr(printer_state, "state", None)
                                if binding.lifecycle_state != "paused_inspection":
                                    binding.lifecycle_state = (
                                        "running"
                                        if printer_state_name == "RUNNING"
                                        else "heating"
                                        if owned_mode == "heat"
                                        else "cooling"
                                    )
                                await db.commit()
                recovering = False
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("External chamber poll failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=POLL_SECONDS)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stopping.set()


external_chamber_manager = ExternalChamberManager()
