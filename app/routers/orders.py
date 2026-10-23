"""Order ingestion and query endpoints."""

import csv
import json
import logging
from json import JSONDecodeError
from pathlib import Path
from uuid import uuid4

from defusedxml.common import DefusedXmlException
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Order, RejectedRecord
from app.parsers.csv_parser import parse_csv
from app.parsers.json_parser import parse_json
from app.parsers.xml_parser import parse_xml
from app.parsers.x12_parser import parse_x12
from app.schemas import IngestionResponse, OrderOut, RejectedRecordOut
from app.services.ingestion_service import process_records

router = APIRouter(prefix="/orders", tags=["orders"])
logger = logging.getLogger("app.orders")
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

PARSERS = {
    ".json": ("json", parse_json),
    ".csv": ("csv", parse_csv),
    ".xml": ("xml", parse_xml),
    ".edi": ("x12", parse_x12),
}


def _result_status(result: dict[str, object]) -> int:
    if not result["accepted"]:
        return status.HTTP_422_UNPROCESSABLE_ENTITY
    if result["rejected"]:
        return status.HTTP_207_MULTI_STATUS
    return status.HTTP_201_CREATED


@router.post("", response_model=IngestionResponse)
async def create_orders(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> IngestionResponse:
    """Accept one JSON order or a list of JSON orders."""
    batch_id = uuid4().hex[:8]
    try:
        payload = await request.json()
    except (JSONDecodeError, UnicodeDecodeError) as error:
        logger.error("batch_id=%s request rejected reasons=malformed JSON body", batch_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must contain valid UTF-8 JSON.",
        ) from error

    if isinstance(payload, dict):
        records = [payload]
    elif isinstance(payload, list):
        records = payload
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be a JSON object or a list of objects.",
        )

    if not records:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must contain at least one order.",
        )

    result = process_records(
        db,
        records,
        source_format="json",
        source_name="api",
        batch_id=batch_id,
    )
    response.status_code = _result_status(result)
    return IngestionResponse.model_validate(result)


@router.post("/upload", response_model=IngestionResponse)
async def upload_orders(
    response: Response,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> IngestionResponse:
    """Parse a supported order file and ingest its records."""
    batch_id = uuid4().hex[:8]
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    parser_entry = PARSERS.get(suffix)
    if parser_entry is None:
        logger.error(
            "batch_id=%s upload rejected reasons=unsupported file type",
            batch_id,
        )
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Supported upload types are .json, .csv, .xml, and .edi.",
        )

    source_format, parser = parser_entry
    try:
        contents = await file.read(MAX_UPLOAD_BYTES + 1)
    finally:
        await file.close()
    if len(contents) > MAX_UPLOAD_BYTES:
        logger.error(
            "batch_id=%s upload rejected reasons=file exceeds 2 MB",
            batch_id,
        )
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Uploaded file must not exceed 2 MB.",
        )

    logger.info(
        "batch_id=%s upload received format=%s size_bytes=%d",
        batch_id,
        source_format,
        len(contents),
    )
    try:
        records = parser(contents)
    except (
        UnicodeDecodeError,
        JSONDecodeError,
        csv.Error,
        ValueError,
        DefusedXmlException,
        SyntaxError,
    ) as error:
        logger.error(
            "batch_id=%s upload parse failed format=%s reasons=%s",
            batch_id,
            source_format,
            error,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not parse {source_format.upper()} upload: {error}",
        ) from error

    result = process_records(
        db,
        records,
        source_format=source_format,
        source_name=filename,
        batch_id=batch_id,
    )
    response.status_code = _result_status(result)
    return IngestionResponse.model_validate(result)


@router.get("/rejected", response_model=list[RejectedRecordOut])
def get_rejected_records(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[RejectedRecordOut]:
    """Return rejected input records, newest first."""
    rows = db.scalars(
        select(RejectedRecord)
        .order_by(RejectedRecord.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        RejectedRecordOut(
            id=row.id,
            batch_id=row.batch_id,
            order_id=row.order_id,
            raw_data=row.raw_data,
            errors=json.loads(row.errors),
        )
        for row in rows
    ]


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: str, db: Session = Depends(get_db)) -> OrderOut:
    """Return one order and its line items."""
    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.order_id == order_id)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found.")

    return OrderOut(
        order_id=order.order_id,
        customer_name=order.customer_name,
        order_date=order.order_date,
        destination=order.destination,
        items=[{"sku": item.sku, "qty": item.qty} for item in order.items],
    )
