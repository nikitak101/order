"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import models  # noqa: F401 - importing registers the models with Base
from app.database import Base, engine
from app.logging_config import configure_logging
from app.routers.orders import router as orders_router
from app.routers.shipments import router as shipments_router
from app.routers.webhooks import router as webhooks_router

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create database tables once when the application starts."""
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Order and Shipment Integration Pipeline",
    lifespan=lifespan,
)
app.include_router(orders_router)
app.include_router(shipments_router)
app.include_router(webhooks_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Confirm that the API process is responding."""
    return {"status": "ok"}
