"""Three owned application variants; no arbitrary project code is loaded.

This is an illustrative order-status API, not a production service or dataset.
The old contract permits additional response properties explicitly.
"""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict


Variant = Literal["before", "breaking", "fixed"]
ROUTE = "/api/v1/orders/{order_id}"
REQUEST_PATH = "/api/v1/orders/order-001"


class OrderResponseBefore(BaseModel):
    model_config = ConfigDict(extra="allow")
    order_id: str
    status: str


class OrderResponseBreaking(BaseModel):
    model_config = ConfigDict(extra="allow")
    order_id: str
    state: str


class OrderResponseFixed(BaseModel):
    model_config = ConfigDict(extra="allow")
    order_id: str
    status: str
    state: str


MODELS = {
    "before": OrderResponseBefore,
    "breaking": OrderResponseBreaking,
    "fixed": OrderResponseFixed,
}


def create_app(variant: Variant = "before") -> FastAPI:
    if variant not in MODELS:
        raise ValueError("unsupported owned example variant")
    app = FastAPI(
        title="WritScope owned order-status example",
        version="1.0.0",
        description="Illustrative local example; not production data.",
    )

    @app.get(ROUTE, response_model=MODELS[variant])
    def order_status(order_id: str):
        if variant == "before":
            return {"order_id": order_id, "status": "processing"}
        if variant == "breaking":
            return {"order_id": order_id, "state": "processing"}
        return {"order_id": order_id, "status": "processing", "state": "processing"}

    return app


def old_client(payload: dict) -> dict:
    """The existing consumer needs status and accepts additional fields."""
    return {"order_id": payload["order_id"], "display_status": payload["status"]}


def new_client(payload: dict) -> dict:
    """The new consumer uses state; changing its need does not grant authority."""
    return {"order_id": payload["order_id"], "display_status": payload["state"]}


app = create_app()
