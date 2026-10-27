"""MQTT Relay Service for publishing BamBuddy events to external MQTT brokers.

This service enables integration with external automation systems like
Node-RED, Home Assistant, and other MQTT-based platforms.
"""

import asyncio
import json
import logging
import math
import ssl
import threading
import time
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt
from sqlalchemy import select

from backend.app.core.database import async_session
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.printer_manager import get_derived_status_name
from backend.app.utils.ams_humidity import ams_humidity_percent
from backend.app.utils.paho_teardown import retire_paho_client

logger = logging.getLogger(__name__)


def _relay_int(value: Any, default: int = 0) -> int:
    """Parse printer JSON integers without letting one malformed field drop a relay update."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _relay_float(value: Any) -> float | None:
    """Parse a finite numeric value while rejecting bools and malformed strings."""
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _relay_global_tray_id(ams_id: int, tray_id: int) -> int:
    """Return Bambuddy's stable global tray id for regular, HT, and external slots."""
    if ams_id >= 255:
        return 254 + tray_id
    if ams_id >= 128:
        return ams_id
    return ams_id * 4 + tray_id


def _build_ams_relay_status(state: Any, linked_spools: dict[int, dict[str, Any]] | None = None) -> dict:
    """Build a compact, stable AMS snapshot suitable for small MQTT consumers."""
    raw_data = getattr(state, "raw_data", None) or {}
    tray_now = _relay_int(getattr(state, "tray_now", 255), 255)
    linked_spools = linked_spools or {}
    units = []

    raw_units = raw_data.get("ams")
    if isinstance(raw_units, list):
        for raw_unit in raw_units:
            if not isinstance(raw_unit, dict):
                continue
            ams_id = _relay_int(raw_unit.get("id"))
            trays = []
            raw_trays = raw_unit.get("tray")
            if isinstance(raw_trays, list):
                for raw_tray in raw_trays:
                    if not isinstance(raw_tray, dict):
                        continue
                    tray_id = _relay_int(raw_tray.get("id"))
                    global_id = _relay_global_tray_id(ams_id, tray_id)
                    tray_payload = {
                        "id": tray_id,
                        "global_id": global_id,
                        "exists": raw_tray.get("exists"),
                        "active": global_id == tray_now,
                        "type": raw_tray.get("tray_type") or "",
                        "sub_brand": raw_tray.get("tray_sub_brands") or "",
                        "name": raw_tray.get("tray_id_name") or "",
                        "color": raw_tray.get("tray_color") or "",
                        "remaining_percent": _relay_int(raw_tray.get("remain")),
                    }
                    if linked_spool := linked_spools.get(global_id):
                        tray_payload.update(linked_spool)
                    trays.append(tray_payload)

            humidity = ams_humidity_percent(raw_unit)
            units.append(
                {
                    "id": ams_id,
                    "temperature": raw_unit.get("temp"),
                    "humidity_percent": round(humidity) if humidity is not None else None,
                    "is_ams_ht": len(trays) == 1,
                    "trays": trays,
                }
            )

    external_trays = []
    raw_external = raw_data.get("vt_tray")
    if isinstance(raw_external, dict):
        raw_external = [raw_external]
    if isinstance(raw_external, list):
        for raw_tray in raw_external:
            if not isinstance(raw_tray, dict):
                continue
            tray_id = _relay_int(raw_tray.get("id"), 254)
            tray_payload = {
                "id": tray_id,
                "active": tray_id == tray_now,
                "type": raw_tray.get("tray_type") or "",
                "sub_brand": raw_tray.get("tray_sub_brands") or "",
                "name": raw_tray.get("tray_id_name") or "",
                "color": raw_tray.get("tray_color") or "",
                "remaining_percent": _relay_int(raw_tray.get("remain")),
            }
            if linked_spool := linked_spools.get(tray_id):
                tray_payload.update(linked_spool)
            external_trays.append(tray_payload)

    return {
        "tray_now": tray_now,
        "status_main": _relay_int(getattr(state, "ams_status_main", 0)),
        "status_sub": _relay_int(getattr(state, "ams_status_sub", 0)),
        "filament_backup": getattr(state, "ams_filament_backup", None),
        "units": units,
        "external_trays": external_trays,
    }


def _build_extruder_relay_status(state: Any) -> dict:
    """Build physical filament-sensor and per-hotend feed state."""
    slots = {}
    for extruder_id, slot in (getattr(state, "extruder_slots", None) or {}).items():
        slots[str(extruder_id)] = {
            "ams_id": getattr(slot, "ams_id", None),
            "tray_id": getattr(slot, "slot_id", None),
            "filament_present": bool(getattr(slot, "has_filament", False)),
        }
    connected = bool(getattr(state, "connected", False))
    return {
        "active": _relay_int(getattr(state, "active_extruder", 0)),
        "filament_present": getattr(state, "filament_at_extruder", None) if connected else None,
        "slots": slots,
    }


class MQTTRelayService:
    """Publishes BamBuddy events to an external MQTT broker."""

    # Minimum interval between status updates per printer (seconds)
    STATUS_THROTTLE_SECONDS = 1.0
    LINKED_SPOOL_CACHE_SECONDS = 10.0

    def __init__(self):
        self.client: mqtt.Client | None = None
        self.enabled = False
        self.connected = False
        self.topic_prefix = "bambuddy"
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._broker = ""
        self._port = 1883
        self._last_printer_status: dict[int, float] = {}  # printer_id -> last publish timestamp
        self._linked_spool_cache: dict[int, tuple[float, dict[int, dict[str, Any]]]] = {}
        self._smart_plug_service = None  # Lazy import to avoid circular dependency
        self._settings: dict = {}  # Store settings for smart plug service
        self._disconnection_event: threading.Event | None = None

    async def configure(self, settings: dict) -> bool:
        """Configure MQTT connection from settings.

        Returns True if connection was successful or MQTT is disabled.
        """
        self.enabled = settings.get("mqtt_enabled", False)
        self._settings = settings  # Store for smart plug service

        if not self.enabled:
            await self.disconnect()
            # Also configure smart plug service (will disable it)
            await self._configure_smart_plug_service(settings)
            logger.info("MQTT relay disabled")
            return True

        broker = settings.get("mqtt_broker", "")
        port = settings.get("mqtt_port", 1883)
        username = settings.get("mqtt_username", "")
        password = settings.get("mqtt_password", "")
        self.topic_prefix = settings.get("mqtt_topic_prefix", "bambuddy")
        use_tls = settings.get("mqtt_use_tls", False)

        if not broker:
            logger.warning("MQTT enabled but no broker configured")
            return False

        # Store for status endpoint
        self._broker = broker
        self._port = port

        # Disconnect existing connection if settings changed
        if self.client:
            await self.disconnect()

        # Create and connect client
        result = await self._connect(broker, port, username, password, use_tls)

        # Configure smart plug service with same settings
        await self._configure_smart_plug_service(settings)

        return result

    async def _configure_smart_plug_service(self, settings: dict):
        """Configure the MQTT smart plug service with the same broker settings."""
        try:
            if self._smart_plug_service is None:
                from backend.app.services.mqtt_smart_plug import mqtt_smart_plug_service

                self._smart_plug_service = mqtt_smart_plug_service

            await self._smart_plug_service.configure(settings)
        except Exception as e:
            logger.error("Failed to configure MQTT smart plug service: %s", e)

    @property
    def smart_plug_service(self):
        """Get the MQTT smart plug service instance."""
        if self._smart_plug_service is None:
            from backend.app.services.mqtt_smart_plug import mqtt_smart_plug_service

            self._smart_plug_service = mqtt_smart_plug_service
        return self._smart_plug_service

    async def _connect(self, broker: str, port: int, username: str, password: str, use_tls: bool) -> bool:
        """Establish MQTT connection."""
        try:
            # Create client with callback API version 2 (use MQTTv311 for broader compatibility)
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"bambuddy-{id(self)}",
                protocol=mqtt.MQTTv311,
            )

            # Set up callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect

            # Configure authentication
            if username:
                self.client.username_pw_set(username, password)

            # Configure TLS (allow self-signed certs for testing)
            if use_tls:
                self.client.tls_set(cert_reqs=ssl.CERT_NONE)
                self.client.tls_insecure_set(True)  # Allow self-signed certs

            # Run connect_async in thread pool with timeout to avoid blocking
            # on unreachable brokers (connect_async does synchronous socket creation)
            try:
                await asyncio.wait_for(asyncio.to_thread(self.client.connect_async, broker, port, 60), timeout=3.0)
            except TimeoutError:
                logger.warning("MQTT relay connection to %s:%s timed out", broker, port)
                return False

            self.client.loop_start()

            # Wait briefly for connection callback
            await asyncio.sleep(1.0)

            if self.connected:
                logger.info("MQTT relay connected to %s:%s", broker, port)
                # Publish online status
                self._publish_status("online")
                return True
            else:
                logger.warning("MQTT relay connection pending to %s:%s", broker, port)
                return True  # Connection is async, may succeed later

        except Exception as e:
            logger.error("MQTT relay connection failed: %s", e)
            self.connected = False
            return False

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: dict,
        reason_code: int | mqtt.ReasonCode,
        properties: mqtt.Properties | None = None,
    ):
        """Callback when connected to broker."""
        # Handle both MQTTv311 (int) and MQTTv5 (ReasonCode) return codes
        rc = reason_code if isinstance(reason_code, int) else reason_code.value
        if rc == 0:
            self.connected = True
            logger.info("MQTT relay connected successfully")
            # Publish online status
            self._publish_status("online")
        else:
            self.connected = False
            logger.error("MQTT relay connection failed: %s", reason_code)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags_or_rc: dict | int | mqtt.ReasonCode,
        reason_code: int | mqtt.ReasonCode | None = None,
        properties: mqtt.Properties | None = None,
    ):
        """Callback when disconnected from broker."""
        self.connected = False
        # Handle both MQTTv311 (rc as 3rd param) and MQTTv5 (flags, rc, props)
        rc = reason_code if reason_code is not None else flags_or_rc
        rc_val = rc if isinstance(rc, int) else getattr(rc, "value", 0)
        if rc_val != 0:
            logger.warning("MQTT relay disconnected: %s", rc)
        else:
            logger.info("MQTT relay disconnected cleanly")
        if self._disconnection_event:
            self._disconnection_event.set()

    async def disconnect(self, timeout: float = 0):
        """Disconnect from MQTT broker."""
        if self.client:
            try:
                # Publish offline status before disconnecting
                self._publish_status("offline")
                self._disconnection_event = threading.Event()
                self.client.disconnect()
                await asyncio.to_thread(self._disconnection_event.wait, timeout=timeout)
                retire_paho_client(self.client, "relay")
            except Exception as e:
                logger.debug("MQTT disconnect error (ignored): %s", e)
            finally:
                self.client = None
                self.connected = False

    def _publish_status(self, status: str):
        """Publish BamBuddy status (online/offline)."""
        self._publish(
            f"{self.topic_prefix}/status",
            {"status": status, "timestamp": datetime.now(timezone.utc).isoformat()},
            retain=True,
        )

    def _publish(self, topic: str, payload: dict, retain: bool = False):
        """Publish message to MQTT broker."""
        if not self.client or not self.connected:
            return

        try:
            with self._lock:
                self.client.publish(topic, json.dumps(payload, default=str), qos=1, retain=retain)
        except Exception as e:
            logger.debug("MQTT publish error: %s", e)

    def get_status(self) -> dict:
        """Get current MQTT relay status for API."""
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "broker": self._broker if self.enabled else "",
            "port": self._port if self.enabled else 0,
            "topic_prefix": self.topic_prefix,
        }

    # =========================================================================
    # Printer Events
    # =========================================================================

    async def _get_linked_spools(self, printer_id: int) -> dict[int, dict[str, Any]]:
        """Return global tray id -> compact linked-spool values, with a short cache."""
        cached = self._linked_spool_cache.get(printer_id)
        now = time.monotonic()
        if cached and now - cached[0] < self.LINKED_SPOOL_CACHE_SECONDS:
            return cached[1]

        try:
            async with async_session() as db:
                from backend.app.services.inventory_mode import spoolman_owns_assignments

                if await spoolman_owns_assignments(db):
                    assignment_result = await db.execute(
                        select(
                            SpoolmanSlotAssignment.ams_id,
                            SpoolmanSlotAssignment.tray_id,
                            SpoolmanSlotAssignment.spoolman_spool_id,
                        ).where(SpoolmanSlotAssignment.printer_id == printer_id)
                    )
                    assignments = list(assignment_result.all())
                    linked = {
                        _relay_global_tray_id(row.ams_id, row.tray_id): {
                            "spool_id": row.spoolman_spool_id,
                            "spool_weight": None,
                            "spool_weight_percent": None,
                        }
                        for row in assignments
                    }

                    if assignments:
                        from backend.app.services.spoolman import get_spoolman_client

                        client = await get_spoolman_client()
                        if client is not None:
                            try:
                                fetched_spools = await asyncio.wait_for(client.get_spools(), timeout=2.0)
                            except Exception as exc:  # noqa: BLE001 - keep spool IDs when Spoolman is unavailable
                                logger.debug("Could not fetch Spoolman weights for MQTT status: %s", exc)
                            else:
                                spools = {_relay_int(spool.get("id"), -1): spool for spool in fetched_spools}
                                for row in assignments:
                                    spool = spools.get(row.spoolman_spool_id)
                                    if not spool:
                                        continue
                                    filament = spool.get("filament") or {}
                                    total = _relay_float(spool.get("initial_weight"))
                                    if not total or total <= 0:
                                        total = _relay_float(filament.get("weight"))
                                    remaining = _relay_float(spool.get("remaining_weight"))
                                    if remaining is None:
                                        used = _relay_float(spool.get("used_weight"))
                                        if total is not None and total > 0 and used is not None:
                                            remaining = max(0.0, total - used)
                                    global_id = _relay_global_tray_id(row.ams_id, row.tray_id)
                                    if remaining is not None:
                                        linked[global_id]["spool_weight"] = round(max(0.0, remaining))
                                    if total is not None and total > 0 and remaining is not None:
                                        linked[global_id]["spool_weight_percent"] = round(
                                            min(100.0, max(0.0, remaining / total * 100))
                                        )
                else:
                    assignment_result = await db.execute(
                        select(
                            SpoolAssignment.ams_id,
                            SpoolAssignment.tray_id,
                            Spool.id,
                            Spool.label_weight,
                            Spool.weight_used,
                        )
                        .join(Spool, Spool.id == SpoolAssignment.spool_id)
                        .where(SpoolAssignment.printer_id == printer_id)
                    )
                    linked = {}
                    for row in assignment_result.all():
                        total = float(row.label_weight or 0)
                        remaining = max(0.0, total - float(row.weight_used or 0))
                        linked[_relay_global_tray_id(row.ams_id, row.tray_id)] = {
                            "spool_id": row.id,
                            "spool_weight": round(remaining),
                            "spool_weight_percent": round(min(100.0, remaining / total * 100)) if total > 0 else 0,
                        }
        except Exception as exc:  # noqa: BLE001 - status publishing must survive inventory failures
            logger.debug("Could not enrich MQTT status with linked spools for printer %s: %s", printer_id, exc)
            return cached[1] if cached else {}

        self._linked_spool_cache[printer_id] = (now, linked)
        return linked

    async def on_printer_status(
        self,
        printer_id: int,
        state: Any,
        printer_name: str,
        printer_serial: str,
        awaiting_plate_clear: bool = False,
        printer_model: str | None = None,
    ):
        """Publish printer status change (throttled to 1 update/sec per printer)."""
        if not self.enabled or not self.connected:
            return

        # Throttle status updates to avoid flooding MQTT broker
        now = time.time()
        last_publish = self._last_printer_status.get(printer_id, 0)
        if now - last_publish < self.STATUS_THROTTLE_SECONDS:
            return  # Skip this update, too soon since last publish
        self._last_printer_status[printer_id] = now

        linked_spools = await self._get_linked_spools(printer_id)

        # Build status payload from PrinterState
        payload = {
            "printer_id": printer_id,
            "printer_name": printer_name,
            "printer_serial": printer_serial,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "connected": state.connected,
            "state": state.state,
            "stg_cur_name": get_derived_status_name(state, printer_model),
            "progress": state.progress,
            "remaining_time": state.remaining_time,
            "layer_num": state.layer_num,
            "total_layers": state.total_layers,
            "current_print": state.current_print,
            "subtask_name": state.subtask_name,
            "gcode_file": state.gcode_file,
            "temperatures": state.temperatures,
            "wifi_signal": state.wifi_signal,
            "chamber_light": state.chamber_light,
            "speed_level": state.speed_level,
            "cooling_fan_speed": state.cooling_fan_speed,
            "big_fan1_speed": state.big_fan1_speed,
            "big_fan2_speed": state.big_fan2_speed,
            "heatbreak_fan_speed": state.heatbreak_fan_speed,
            "left_aux_fan_speed": state.left_aux_fan_speed,
            "exhaust_fan_present": state.exhaust_fan_present,
            # Compact snapshots for dashboards and embedded displays. These are
            # deliberately normalized rather than forwarding firmware-specific
            # raw MQTT keys, so P1/X1/H2 families share one consumer contract.
            "ams": _build_ams_relay_status(state, linked_spools),
            "extruder": _build_extruder_relay_status(state),
            # Bambuddy-side gate, not printer telemetry (#2525). Mirrors what the
            # Web UI already receives via printer_state_to_dict, so an external
            # automation can tell "finished" from "finished and still waiting for
            # someone to clear the bed". Edge changes are also published on
            # printers/{serial}/plate_clear — this topic only refreshes when the
            # printer pushes telemetry, which stops entirely after Auto Off.
            "awaiting_plate_clear": awaiting_plate_clear,
        }

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/status",
            payload,
            retain=True,
        )

    async def on_plate_clear_state(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        awaiting: bool,
    ):
        """Publish the plate-clear gate as it flips (#2525).

        Retained, unlike the other per-printer event topics, because this is a
        *state* an automation needs on subscribe rather than a moment it might
        have missed. The status topic carries the same field, but only refreshes
        when the printer pushes telemetry — after Auto Off cycles the printer the
        retained status payload would sit at ``awaiting_plate_clear: false``
        indefinitely while the gate is in fact still up.
        """
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/plate_clear",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "awaiting": awaiting,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            retain=True,
        )

    async def on_printer_online(self, printer_id: int, printer_name: str, printer_serial: str):
        """Publish printer came online event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/online",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_printer_offline(self, printer_id: int, printer_name: str, printer_serial: str):
        """Publish printer went offline event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/offline",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_print_start(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        filename: str,
        subtask_name: str,
    ):
        """Publish print started event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/print/started",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "filename": filename,
                "subtask_name": subtask_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_print_complete(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        filename: str,
        subtask_name: str,
        status: str,
    ):
        """Publish print completed event."""
        if not self.enabled or not self.connected:
            return

        # Determine topic based on status
        if status == "completed":
            topic = f"{self.topic_prefix}/printers/{printer_serial}/print/completed"
        else:
            topic = f"{self.topic_prefix}/printers/{printer_serial}/print/failed"

        self._publish(
            topic,
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "filename": filename,
                "subtask_name": subtask_name,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_ams_change(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        ams_data: list,
    ):
        """Publish AMS filament change event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/ams/changed",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "ams_units": ams_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_printer_error(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        errors: list,
    ):
        """Publish printer HMS error event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/error",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "errors": errors,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    # =========================================================================
    # Print Queue Events
    # =========================================================================

    async def on_queue_job_added(
        self,
        job_id: int,
        filename: str,
        printer_id: int | None,
        printer_name: str | None,
    ):
        """Publish job added to queue event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/queue/job_added",
            {
                "job_id": job_id,
                "filename": filename,
                "printer_id": printer_id,
                "printer_name": printer_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_queue_job_started(
        self,
        job_id: int,
        filename: str,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
    ):
        """Publish queued job started printing event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/queue/job_started",
            {
                "job_id": job_id,
                "filename": filename,
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_queue_job_completed(
        self,
        job_id: int,
        filename: str,
        printer_id: int,
        printer_name: str,
        status: str,
    ):
        """Publish queued job finished event."""
        if not self.enabled or not self.connected:
            return

        topic = (
            f"{self.topic_prefix}/queue/job_completed"
            if status == "completed"
            else f"{self.topic_prefix}/queue/job_failed"
        )

        self._publish(
            topic,
            {
                "job_id": job_id,
                "filename": filename,
                "printer_id": printer_id,
                "printer_name": printer_name,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    # =========================================================================
    # Maintenance Events
    # =========================================================================

    async def on_maintenance_alert(
        self,
        printer_id: int,
        printer_name: str,
        maintenance_type: str,
        current_value: float,
        threshold: float,
    ):
        """Publish maintenance alert triggered event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/maintenance/alert",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "maintenance_type": maintenance_type,
                "current_value": current_value,
                "threshold": threshold,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_maintenance_acknowledged(
        self,
        printer_id: int,
        printer_name: str,
        maintenance_type: str,
    ):
        """Publish maintenance alert acknowledged event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/maintenance/acknowledged",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "maintenance_type": maintenance_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_maintenance_reset(
        self,
        printer_id: int,
        printer_name: str,
        maintenance_type: str,
    ):
        """Publish maintenance counter reset event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/maintenance/reset",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "maintenance_type": maintenance_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    # =========================================================================
    # Archive Events
    # =========================================================================

    async def on_archive_created(
        self,
        archive_id: int,
        print_name: str,
        printer_name: str,
        status: str,
    ):
        """Publish print archived event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/archive/created",
            {
                "archive_id": archive_id,
                "print_name": print_name,
                "printer_name": printer_name,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_archive_updated(
        self,
        archive_id: int,
        print_name: str,
        status: str,
    ):
        """Publish archive record updated event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/archive/updated",
            {
                "archive_id": archive_id,
                "print_name": print_name,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    # =========================================================================
    # Filament/Spoolman Events
    # =========================================================================

    async def on_filament_low(
        self,
        spool_id: int,
        spool_name: str,
        remaining_weight: float,
        remaining_percent: float,
    ):
        """Publish filament inventory low event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/filament/low",
            {
                "spool_id": spool_id,
                "spool_name": spool_name,
                "remaining_weight": remaining_weight,
                "remaining_percent": remaining_percent,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    # =========================================================================
    # Smart Plug Events
    # =========================================================================

    async def on_smart_plug_state(
        self,
        plug_id: int,
        plug_name: str,
        state: str,
        printer_id: int | None = None,
        printer_name: str | None = None,
    ):
        """Publish smart plug state change event."""
        if not self.enabled or not self.connected:
            return

        topic = f"{self.topic_prefix}/smart_plugs/on" if state == "on" else f"{self.topic_prefix}/smart_plugs/off"

        self._publish(
            topic,
            {
                "plug_id": plug_id,
                "plug_name": plug_name,
                "state": state,
                "printer_id": printer_id,
                "printer_name": printer_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_smart_plug_energy(
        self,
        plug_id: int,
        plug_name: str,
        power: float,
        energy_today: float,
        energy_total: float,
    ):
        """Publish smart plug energy update event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/smart_plugs/energy",
            {
                "plug_id": plug_id,
                "plug_name": plug_name,
                "power_watts": power,
                "energy_today_kwh": energy_today,
                "energy_total_kwh": energy_total,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )


# Global instance
mqtt_relay = MQTTRelayService()
