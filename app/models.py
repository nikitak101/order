"""SQLAlchemy models for orders, shipments, and rejected input."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Order(Base):
    """An order that passed input validation."""

    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    destination: Mapped[str] = mapped_column(String(300), nullable=False)
    items: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    shipment: Mapped["Shipment | None"] = relationship(
        back_populates="order", cascade="all, delete-orphan", uselist=False
    )


class OrderLine(Base):
    """A SKU and quantity belonging to an accepted order."""

    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    order: Mapped[Order] = relationship(back_populates="items")


class Shipment(Base):
    """Current shipment status for an accepted order."""

    __tablename__ = "shipments"

    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.order_id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CREATED")
    order: Mapped[Order] = relationship(back_populates="shipment")
    events: Mapped[list["ShipmentEvent"]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan"
    )


class ShipmentEvent(Base):
    """A change to a shipment's status."""

    __tablename__ = "shipment_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("shipments.order_id", ondelete="CASCADE"), nullable=False, index=True
    )
    old_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    shipment: Mapped[Shipment] = relationship(back_populates="events")


class RejectedRecord(Base):
    """An invalid or duplicate order record."""

    __tablename__ = "rejected_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_format: Mapped[str] = mapped_column(String(20), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_data: Mapped[str] = mapped_column(Text, nullable=False)
    errors: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebhookSubscription(Base):
    """A URL registered to receive shipment status changes."""

    __tablename__ = "webhook_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
