from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExternalChamberBindingBase(BaseModel):
    printer_id: int
    name: str = Field(min_length=1, max_length=100)
    climate_entity_id: str = Field(pattern=r"^climate\.[a-z0-9_]+$", max_length=255)
    duration_entity_id: str = Field(pattern=r"^number\.[a-z0-9_]+$", max_length=255)
    heater_ready_entity_id: str = Field(pattern=r"^binary_sensor\.[a-z0-9_]+$", max_length=255)
    ventilation_ready_entity_id: str | None = Field(
        default=None, pattern=r"^binary_sensor\.[a-z0-9_]+$", max_length=255
    )
    ventilation_fan_entity_id: str | None = Field(default=None, pattern=r"^fan\.[a-z0-9_]+$", max_length=255)
    enabled: bool = True
    strict: bool = False
    manual_duration_minutes: int = Field(default=240, ge=15, le=1440, multiple_of=15)
    cooling_fan_percentage: int = Field(default=70, ge=0, le=100)

    @field_validator(
        "name",
        "climate_entity_id",
        "duration_entity_id",
        "heater_ready_entity_id",
        "ventilation_ready_entity_id",
        "ventilation_fan_entity_id",
    )
    @classmethod
    def strip_strings(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ExternalChamberBindingCreate(ExternalChamberBindingBase):
    ventilation_ready_entity_id: str = Field(pattern=r"^binary_sensor\.[a-z0-9_]+$", max_length=255)


class ExternalChamberBindingUpdate(BaseModel):
    printer_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    climate_entity_id: str | None = Field(default=None, pattern=r"^climate\.[a-z0-9_]+$", max_length=255)
    duration_entity_id: str | None = Field(default=None, pattern=r"^number\.[a-z0-9_]+$", max_length=255)
    heater_ready_entity_id: str | None = Field(default=None, pattern=r"^binary_sensor\.[a-z0-9_]+$", max_length=255)
    ventilation_ready_entity_id: str | None = Field(
        default=None, pattern=r"^binary_sensor\.[a-z0-9_]+$", max_length=255
    )
    ventilation_fan_entity_id: str | None = Field(default=None, pattern=r"^fan\.[a-z0-9_]+$", max_length=255)
    enabled: bool | None = None
    strict: bool | None = None
    manual_duration_minutes: int | None = Field(default=None, ge=15, le=1440, multiple_of=15)
    cooling_fan_percentage: int | None = Field(default=None, ge=0, le=100)


class ExternalChamberBindingResponse(ExternalChamberBindingBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_queue_item_id: int | None = None
    owned_target: int | None = None
    owned_duration_minutes: int | None = None
    owned_mode: str | None = None
    owned_fan_percentage: int | None = None
    lifecycle_state: str = "idle"
    lease_renewed_at: datetime | None = None
    fault_notified: bool = False
    created_at: datetime
    updated_at: datetime


class ExternalChamberEntity(BaseModel):
    entity_id: str
    friendly_name: str
    domain: str
    state: str | None = None
    attributes: dict = Field(default_factory=dict)


class ExternalChamberTestRequest(BaseModel):
    climate_entity_id: str = Field(pattern=r"^climate\.[a-z0-9_]+$")
    duration_entity_id: str = Field(pattern=r"^number\.[a-z0-9_]+$")
    heater_ready_entity_id: str = Field(pattern=r"^binary_sensor\.[a-z0-9_]+$")
    ventilation_ready_entity_id: str = Field(pattern=r"^binary_sensor\.[a-z0-9_]+$")
    ventilation_fan_entity_id: str | None = Field(default=None, pattern=r"^fan\.[a-z0-9_]+$")


class ExternalChamberTestResponse(BaseModel):
    success: bool
    error: str | None = None
    status: dict | None = None
