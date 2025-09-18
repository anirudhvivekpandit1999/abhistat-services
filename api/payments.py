from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import os
import razorpay
import hmac
import hashlib

router = APIRouter(prefix="/payments", tags=["payments"])

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
else:
    client = None


class CreateOrderRequest(BaseModel):
    amount_rupees: int = 1
    currency: str = "INR"
    receipt: str | None = None
    notes: dict | None = None


@router.post("/create-order")
def create_order(payload: CreateOrderRequest):
    if client is None:
        raise HTTPException(status_code=500, detail="Razorpay not configured")
    amount_paise = int(payload.amount_rupees) * 100
    data = {
        "amount": amount_paise,
        "currency": payload.currency,
        "payment_capture": 1,
    }
    if payload.receipt:
        data["receipt"] = payload.receipt
    if payload.notes:
        data["notes"] = payload.notes
    order = client.order.create(data=data)
    return {"order": order, "key_id": RAZORPAY_KEY_ID}


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/verify")
def verify_payment(payload: VerifyPaymentRequest):
    if client is None:
        raise HTTPException(status_code=500, detail="Razorpay not configured")
    generated_signature = hmac.new(
        bytes(RAZORPAY_KEY_SECRET, 'utf-8'),
        bytes(payload.razorpay_order_id + '|' + payload.razorpay_payment_id, 'utf-8'),
        hashlib.sha256
    ).hexdigest()
    if generated_signature != payload.razorpay_signature:
        raise HTTPException(status_code=400, detail="Invalid signature")
    payment = client.payment.fetch(payload.razorpay_payment_id)
    return {"status": "verified", "payment": payment}


