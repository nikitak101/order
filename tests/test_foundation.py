import json
import asyncio
import hashlib
import hmac
from pathlib import Path
import time
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.models import Shipment
from sqlalchemy import select
from app.services import webhook_service


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def valid_order(order_id: str) -> dict[str, object]:
    return {
        "order_id": order_id,
        "customer_name": "Test Customer",
        "order_date": "2026-10-23",
        "destination": "Seattle",
        "items": [{"sku": "SKU-1", "qty": 2}],
    }


def test_create_order_and_read_order(client: TestClient, test_session_factory) -> None:
    order_id = f"TEST-{uuid4().hex[:10]}"
    response = client.post("/orders", json=valid_order(order_id))
    fetched = client.get(f"/orders/{order_id}")

    assert response.status_code == 201
    assert response.json()["accepted"] == [order_id]
    assert fetched.status_code == 200
    assert fetched.json()["items"] == [{"sku": "SKU-1", "qty": 2}]

    with test_session_factory() as db:
        shipment = db.scalar(select(Shipment).where(Shipment.order_id == order_id))
        assert shipment is not None
        assert shipment.status == "CREATED"


def test_partial_invalid_and_duplicate_orders_are_rejected(client: TestClient) -> None:
    existing_id = f"TEST-{uuid4().hex[:10]}"
    duplicate_order = valid_order(existing_id)
    accepted_id = f"TEST-{uuid4().hex[:10]}"
    accepted_order = valid_order(accepted_id)
    invalid_id = f"BAD-{uuid4().hex[:8]}"
    invalid_order = valid_order(invalid_id)
    invalid_order["order_date"] = "2026-10-23T10:30:00"

    first = client.post("/orders", json=duplicate_order)
    partial = client.post(
        "/orders",
        json=[accepted_order, invalid_order],
    )
    all_rejected = client.post("/orders", json=duplicate_order)
    rejected = client.get("/orders/rejected?limit=10&offset=0")

    assert first.status_code == 201
    assert partial.status_code == 207
    assert partial.json()["accepted"] == [accepted_id]
    assert len(partial.json()["rejected"]) == 1
    assert all_rejected.status_code == 422
    assert rejected.status_code == 200
    assert any(row["order_id"] == invalid_id for row in rejected.json())


def test_malformed_json_returns_400(client: TestClient) -> None:
    response = client.post(
        "/orders",
        content=b'{"order_id":',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400


def test_sample_order_files_are_valid_json_documents(client: TestClient) -> None:
    samples = Path(__file__).resolve().parent.parent / "sample_data"

    for filename in ("valid_order.json", "invalid_order.json"):
        with (samples / filename).open(encoding="utf-8") as sample_file:
            assert isinstance(json.load(sample_file), dict)

    with (samples / "invalid_order.json").open(encoding="utf-8") as sample_file:
        response = client.post("/orders", content=sample_file.read())

    assert response.status_code == 422
    assert response.json()["rejected"][0]["errors"]


def test_json_upload_returns_partial_success(client: TestClient) -> None:
    sample_path = Path(__file__).resolve().parent.parent / "sample_data" / "orders.json"
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    payload[0]["order_id"] = f"TEST-{uuid4().hex[:10]}"

    response = client.post(
        "/orders/upload",
        files={
            "file": (
                "orders.json",
                json.dumps(payload).encode("utf-8"),
                "application/json",
            )
        },
    )

    assert response.status_code == 207
    assert response.json()["accepted"] == [payload[0]["order_id"]]
    assert response.json()["rejected"][0]["order_id"] == "ORD-UPLOAD-BAD"


def test_csv_upload_preserves_invalid_quantity_for_validation(client: TestClient) -> None:
    sample_path = Path(__file__).resolve().parent.parent / "sample_data" / "orders.csv"
    response = client.post(
        "/orders/upload",
        files={"file": ("orders.csv", sample_path.read_bytes(), "text/csv")},
    )

    assert response.status_code == 422
    assert response.json()["rejected"][0]["order_id"] == "ORD-CSV-BAD"
    assert response.json()["rejected"][0]["errors"][0]["field"] == "items.0.qty"


def test_xml_upload_succeeds_and_broken_xml_returns_400(client: TestClient) -> None:
    sample_directory = Path(__file__).resolve().parent.parent / "sample_data"
    xml_payload = (sample_directory / "orders.xml").read_text(encoding="utf-8")
    order_id = f"TEST-{uuid4().hex[:10]}"
    xml_payload = xml_payload.replace("ORD-XML-001", order_id)

    accepted = client.post(
        "/orders/upload",
        files={
            "file": ("orders.xml", xml_payload.encode("utf-8"), "application/xml")
        },
    )
    malformed = client.post(
        "/orders/upload",
        files={
            "file": (
                "broken.xml",
                (sample_directory / "broken.xml").read_bytes(),
                "application/xml",
            )
        },
    )

    assert accepted.status_code == 201
    assert accepted.json()["accepted"] == [order_id]
    assert malformed.status_code == 400


def test_upload_rejects_unsupported_type_large_file_and_bad_encoding(
    client: TestClient,
) -> None:
    unsupported = client.post(
        "/orders/upload",
        files={"file": ("unsupported.pdf", b"dummy", "application/pdf")},
    )
    too_large = client.post(
        "/orders/upload",
        files={
            "file": (
                "large.json",
                b" " * (2 * 1024 * 1024 + 1),
                "application/json",
            )
        },
    )
    bad_encoding = client.post(
        "/orders/upload",
        files={"file": ("orders.csv", b"\xff\xfe", "text/csv")},
    )

    assert unsupported.status_code == 415
    assert too_large.status_code == 413
    assert bad_encoding.status_code == 400


def create_test_order(client: TestClient) -> str:
    order_id = f"SHIP-{uuid4().hex[:10]}"
    response = client.post("/orders", json=valid_order(order_id))
    assert response.status_code == 201
    return order_id


def test_shipment_can_transition_from_created_to_delivered_and_has_history(
    client: TestClient,
) -> None:
    transitions = ["PICKED", "SHIPPED", "IN_TRANSIT", "DELIVERED"]
    order_id = create_test_order(client)
    responses = [
        client.patch(
            f"/shipments/{order_id}/status",
            json={"status": next_status},
        )
        for next_status in transitions
    ]
    history = client.get(f"/shipments/{order_id}/history")

    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    assert responses[-1].json()["status"] == "DELIVERED"
    assert history.status_code == 200
    assert [event["new_status"] for event in history.json()] == transitions
    assert history.json()[0]["old_status"] == "CREATED"


def test_shipment_invalid_status_unknown_order_and_illegal_transitions(
    client: TestClient,
) -> None:
    order_id = create_test_order(client)
    invalid_status = client.patch(
        f"/shipments/{order_id}/status",
        json={"status": "ON_HOLD"},
    )
    unknown_order = client.patch(
        "/shipments/UNKNOWN-ORDER/status",
        json={"status": "PICKED"},
    )
    skip_step = client.patch(
        f"/shipments/{order_id}/status",
        json={"status": "SHIPPED"},
    )
    picked = client.patch(
        f"/shipments/{order_id}/status",
        json={"status": "PICKED"},
    )
    return_before_delivery = client.patch(
        f"/shipments/{order_id}/status",
        json={"status": "RETURNED"},
    )
    history = client.get(f"/shipments/{order_id}/history")

    assert invalid_status.status_code == 400
    assert unknown_order.status_code == 404
    assert skip_step.status_code == 409
    assert "Allowed next status: PICKED" in skip_step.json()["detail"]
    assert picked.status_code == 200
    assert return_before_delivery.status_code == 409
    assert [event["new_status"] for event in history.json()] == ["PICKED"]


def test_shipment_history_returns_404_for_unknown_order(client: TestClient) -> None:
    response = client.get("/shipments/UNKNOWN-ORDER/history")

    assert response.status_code == 404


def test_returned_status_is_allowed_only_after_delivery(client: TestClient) -> None:
    transitions = ["PICKED", "SHIPPED", "IN_TRANSIT", "DELIVERED"]
    order_id = create_test_order(client)
    for next_status in transitions:
        response = client.patch(
            f"/shipments/{order_id}/status",
            json={"status": next_status},
        )
        assert response.status_code == 200

    returned = client.patch(
        f"/shipments/{order_id}/status",
        json={"status": "RETURNED"},
    )

    assert returned.status_code == 200
    assert returned.json()["status"] == "RETURNED"


def test_webhook_subscription_blocks_local_urls_and_prevents_duplicates(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(config, "ALLOW_LOCAL_WEBHOOKS", False)
    monkeypatch.setattr(webhook_service.socket, "getaddrinfo", lambda *args, **kwargs: [
        (None, None, None, None, ("93.184.216.34", 80))
    ])
    local = client.post(
        "/webhooks/subscribe",
        json={"url": "http://localhost/webhook-receiver"},
    )
    private_ip = client.post(
        "/webhooks/subscribe",
        json={"url": "http://10.20.30.40/webhook-receiver"},
    )
    invalid_scheme = client.post(
        "/webhooks/subscribe",
        json={"url": "ftp://example.com/hook"},
    )

    assert local.status_code == 400
    assert private_ip.status_code == 400
    assert invalid_scheme.status_code == 400

    monkeypatch.setattr(config, "ALLOW_LOCAL_WEBHOOKS", True)
    callback_url = f"http://localhost/{uuid4().hex}"
    first = client.post("/webhooks/subscribe", json={"url": callback_url})
    duplicate = client.post("/webhooks/subscribe", json={"url": callback_url})

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False


def test_status_change_delivers_signed_webhook_to_builtin_receiver(
    client: TestClient, monkeypatch, caplog
) -> None:
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(config, "ALLOW_LOCAL_WEBHOOKS", True)
    real_async_client = httpx.AsyncClient

    def create_in_process_client(**kwargs):
        return real_async_client(
            transport=httpx.ASGITransport(app=app),
            **kwargs,
        )

    monkeypatch.setattr(webhook_service.httpx, "AsyncClient", create_in_process_client)
    callback_url = "http://localhost/webhook-receiver"

    subscribed = client.post("/webhooks/subscribe", json={"url": callback_url})
    order_id = create_test_order(client)
    changed = client.patch(
        f"/shipments/{order_id}/status",
        json={"status": "PICKED"},
    )

    assert subscribed.status_code == 200
    assert changed.status_code == 200
    assert "receiver accepted event=shipment.status_changed order_id=" in caplog.text


def test_webhook_receiver_rejects_bad_signature_and_old_timestamp(
    client: TestClient, monkeypatch
) -> None:
    secret = "test-secret"
    monkeypatch.setattr(config, "WEBHOOK_SECRET", secret)
    body = json.dumps(
        {
            "event": "shipment.status_changed",
            "order_id": "ORD-WEBHOOK-01",
            "old_status": "CREATED",
            "new_status": "PICKED",
            "timestamp": "2026-10-23T00:00:00+00:00",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    stale_timestamp = str(int(time.time()) - 301)
    stale_signature = hmac.new(
        secret.encode(),
        stale_timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    current_timestamp = str(int(time.time()))

    stale = client.post(
        "/webhook-receiver",
        content=body,
        headers={
            "X-Timestamp": stale_timestamp,
            "X-Signature": stale_signature,
        },
    )
    invalid_signature = client.post(
        "/webhook-receiver",
        content=body,
        headers={
            "X-Timestamp": current_timestamp,
            "X-Signature": "0" * 64,
        },
    )

    assert stale.status_code == 401
    assert invalid_signature.status_code == 401


def test_webhook_retry_stops_after_three_attempts(monkeypatch) -> None:
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "test-secret")
    attempts: list[str] = []

    class FailingResponse:
        status_code = 503

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError(
                "service unavailable",
                request=httpx.Request("POST", "https://receiver.example/hook"),
                response=httpx.Response(503),
            )

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def post(self, url: str, **kwargs):
            attempts.append(url)
            return FailingResponse()

    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(webhook_service.httpx, "AsyncClient", lambda **kwargs: FailingClient())
    monkeypatch.setattr(webhook_service.asyncio, "sleep", no_wait)

    asyncio.run(
        webhook_service.deliver_status_event(
            {"event": "shipment.status_changed", "order_id": "ORD-RETRY"},
            ["https://receiver.example/hook"],
        )
    )

    assert len(attempts) == 3


def test_x12_parser_maps_segments_into_standard_order() -> None:
    from app.parsers.x12_parser import parse_x12

    sample = (
        Path(__file__).resolve().parent.parent / "sample_data" / "sample.edi"
    ).read_bytes()
    record = parse_x12(sample)[0]

    assert record == {
        "order_id": "ORD-EDI-001",
        "customer_name": "EDI Sample Customer",
        "order_date": "2026-10-23",
        "destination": "US",
        "items": [{"sku": "SKU-EDI-01", "qty": 2}],
    }


def test_x12_parser_uses_default_delimiters_without_isa() -> None:
    from app.parsers.x12_parser import parse_x12

    data = (
        b"BEG*00*SA*PO-DEFAULT**20261023~"
        b"N1*ST*Default Delimiter Customer~"
        b"N4*Seattle*WA*98101*US~"
        b"PO1*1*1*EA*10.00**VP*SKU-DEFAULT~"
    )

    record = parse_x12(data)[0]

    assert record["order_id"] == "PO-DEFAULT"
    assert record["destination"] == "US"
    assert record["items"] == [{"sku": "SKU-DEFAULT", "qty": 1}]


def test_edi_upload_accepts_order_and_rejects_malformed_edi(client: TestClient) -> None:
    sample_directory = Path(__file__).resolve().parent.parent / "sample_data"
    edi_text = (sample_directory / "sample.edi").read_text(encoding="utf-8")
    order_id = f"EDI-{uuid4().hex[:10]}"
    edi_text = edi_text.replace("ORD-EDI-001", order_id)

    accepted = client.post(
        "/orders/upload",
        files={"file": ("sample.edi", edi_text.encode("utf-8"), "application/EDI-X12")},
    )
    malformed = client.post(
        "/orders/upload",
        files={
            "file": (
                "malformed.edi",
                (sample_directory / "malformed.edi").read_bytes(),
                "application/EDI-X12",
            )
        },
    )

    assert accepted.status_code == 201
    assert accepted.json()["accepted"] == [order_id]
    assert malformed.status_code == 400
