from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class ExternalChamberBinding(Base):
    """One Home Assistant chamber controller assigned to one printer."""

    __tablename__ = "external_chamber_bindings"
    __table_args__ = (
        UniqueConstraint("printer_id", name="uq_external_chamber_printer"),
        UniqueConstraint("climate_entity_id", name="uq_external_chamber_climate"),
        UniqueConstraint("duration_entity_id", name="uq_external_chamber_duration"),
        UniqueConstraint("heater_ready_entity_id", name="uq_external_chamber_heater_ready"),
        UniqueConstraint("ventilation_ready_entity_id", name="uq_external_chamber_ventilation_ready"),
        UniqueConstraint("ventilation_fan_entity_id", name="uq_external_chamber_ventilation_fan"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    climate_entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    heater_ready_entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Nullable only so existing heat-only bindings survive the safe ALTER migration.
    # New bindings require this field before COOL can be used.
    ventilation_ready_entity_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ventilation_fan_entity_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    strict: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    manual_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=240)
    cooling_fan_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=70)

    # Persist queue ownership so restart recovery never mistakes unrelated
    # manual heating for a Bambuddy session.
    owner_queue_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("print_queue.id", ondelete="SET NULL"), nullable=True
    )
    owned_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owned_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owned_mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    owned_fan_percentage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    lease_renewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fault_notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    printer: Mapped["Printer"] = relationship(back_populates="external_chamber_binding")


from backend.app.models.printer import Printer  # noqa: E402
