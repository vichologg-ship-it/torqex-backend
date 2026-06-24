"""
Backend de Torqex Ferretería:
  - Registra los pedidos del checkout.
  - Crea el cobro real en Mercado Pago (Checkout Pro) y redirige al cliente a pagar.
  - Cuando Mercado Pago confirma el pago (webhook), emite la boleta electrónica en Bsale.

Correrlo:
    pip install -r backend/requirements.txt
    python backend/app.py

Lee credenciales desde backend/.env (no se sube a ningún repositorio público).
"""
import os
import re
import json
import time
import requests
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

app = Flask(__name__)
CORS(app)

BSALE_API_TOKEN = os.environ.get("BSALE_API_TOKEN", "")
BSALE_OFFICE_ID = int(os.environ.get("BSALE_OFFICE_ID", "1"))
BSALE_DOCUMENT_TYPE_ID = int(os.environ.get("BSALE_DOCUMENT_TYPE_ID", "1"))
BSALE_IVA_TAX_ID = int(os.environ.get("BSALE_IVA_TAX_ID", "1"))
BSALE_MP_PAYMENT_TYPE_ID = int(os.environ.get("BSALE_MP_PAYMENT_TYPE_ID", "13"))
BSALE_API_BASE = "https://api.bsale.io/v1"

MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")
MP_PUBLIC_KEY = os.environ.get("MP_PUBLIC_KEY", "")
MP_API_BASE = "https://api.mercadopago.com"

SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "http://localhost:8000")
BACKEND_BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://localhost:5000")

ORDERS_FILE = os.path.join(os.path.dirname(__file__), "orders.json")


def load_orders():
    if os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_orders(orders):
    with open(ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)


def find_order(orders, order_id):
    return next((o for o in orders if o["id"] == order_id), None)


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "bsale_configured": bool(BSALE_API_TOKEN),
        "mercadopago_configured": bool(MP_ACCESS_TOKEN),
    })


@app.route("/api/checkout", methods=["POST"])
def checkout():
    """Recibe el carrito + datos del cliente desde checkout.html, registra el
    pedido y crea la preferencia de pago en Mercado Pago. Devuelve el link
    (init_point) al que hay que redirigir al cliente para que pague."""
    data = request.get_json(force=True)
    items = data.get("items", [])
    customer = data.get("customer", {})

    if not items:
        return jsonify({"error": "carrito vacío"}), 400

    orders = load_orders()
    order = {
        "id": len(orders) + 1,
        "items": items,
        "customer": customer,
        "total": sum(i["price"] * i["qty"] for i in items),
        "status": "pendiente_pago",
        "createdAt": int(time.time()),
    }
    orders.append(order)
    save_orders(orders)

    init_point = None
    mp_error = None
    if MP_ACCESS_TOKEN:
        try:
            init_point = crear_preferencia_mercadopago(order)
        except requests.HTTPError as e:
            mp_error = e.response.text
        except Exception as e:
            mp_error = str(e)

    return jsonify({
        "order_id": order["id"],
        "status": order["status"],
        "init_point": init_point,
        "mp_error": mp_error,
    })


def crear_preferencia_mercadopago(order):
    """Crea una preferencia de pago (Checkout Pro) en Mercado Pago para el
    pedido, y devuelve la URL a la que hay que redirigir al cliente."""
    headers = {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    items_mp = [
        {
            "title": item["name"][:250],
            "quantity": item["qty"],
            "unit_price": float(item["price"]),
            "currency_id": "CLP",
        }
        for item in order["items"]
    ]

    customer = order.get("customer", {})
    payload = {
        "items": items_mp,
        "payer": {
            "name": customer.get("firstName", ""),
            "surname": customer.get("lastName", ""),
            "email": customer.get("email", ""),
            "identification": {
                "type": "RUT",
                "number": re.sub(r"[^0-9kK]", "", customer.get("rut", "")),
            },
        },
        "external_reference": str(order["id"]),
        "back_urls": {
            "success": f"{SITE_BASE_URL}/checkout-resultado.html?status=success&order_id={order['id']}",
            "failure": f"{SITE_BASE_URL}/checkout-resultado.html?status=failure&order_id={order['id']}",
            "pending": f"{SITE_BASE_URL}/checkout-resultado.html?status=pending&order_id={order['id']}",
        },
    }

    # Mercado Pago exige que back_urls.success sea un dominio público real
    # para poder usar auto_return (no acepta localhost). Mientras el sitio
    # esté solo en este computador, se omite y el cliente vuelve manualmente.
    if "localhost" not in SITE_BASE_URL and "127.0.0.1" not in SITE_BASE_URL:
        payload["auto_return"] = "approved"

    if "localhost" not in BACKEND_BASE_URL and "127.0.0.1" not in BACKEND_BASE_URL:
        payload["notification_url"] = f"{BACKEND_BASE_URL}/api/mercadopago/webhook"

    resp = requests.post(f"{MP_API_BASE}/checkout/preferences", json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data.get("init_point") or data.get("sandbox_init_point")


def emitir_boleta(order, payment_amount=None, payment_type_id=None):
    """Crea la boleta electrónica en Bsale para un pedido ya pagado."""
    if not BSALE_API_TOKEN:
        raise RuntimeError("Falta configurar BSALE_API_TOKEN en backend/.env")

    headers = {"access_token": BSALE_API_TOKEN, "Content-Type": "application/json"}

    details = []
    for item in order["items"]:
        gross = item["price"]
        net = round(gross / 1.19, 2)  # Precio Venta Bruto ya incluye IVA 19%
        details.append({
            "code": item.get("sku") or "",
            "comment": item["name"],
            "quantity": item["qty"],
            "netUnitValue": net,
            "taxId": [BSALE_IVA_TAX_ID],
        })

    payload = {
        "documentTypeId": BSALE_DOCUMENT_TYPE_ID,
        "officeId": BSALE_OFFICE_ID,
        "emissionDate": int(time.time()),
        "declareSii": 1,
        "details": details,
    }

    customer = order.get("customer", {})
    if customer.get("email"):
        payload["client"] = {
            "firstName": customer.get("firstName", ""),
            "lastName": customer.get("lastName", ""),
            "email": customer.get("email", ""),
        }

    if payment_amount is not None:
        payload["payments"] = [{
            "paymentTypeId": payment_type_id or BSALE_MP_PAYMENT_TYPE_ID,
            "amount": payment_amount,
            "recordDate": int(time.time()),
        }]

    resp = requests.post(f"{BSALE_API_BASE}/documents.json", json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()


@app.route("/api/bsale/document", methods=["POST"])
def bsale_document():
    """Emite manualmente la boleta de un pedido ya registrado (uso interno,
    por ejemplo si el pago se confirmó por transferencia/efectivo en vez de
    Mercado Pago)."""
    order_id = request.get_json(force=True).get("order_id")
    orders = load_orders()
    order = find_order(orders, order_id)
    if not order:
        return jsonify({"error": "pedido no encontrado"}), 404
    try:
        doc = emitir_boleta(order)
        order["status"] = "boleta_emitida"
        order["bsaleDocument"] = doc
        save_orders(orders)
        return jsonify(doc)
    except requests.HTTPError as e:
        return jsonify({"error": "bsale_error", "detail": e.response.text}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mercadopago/webhook", methods=["GET", "POST"])
def mercadopago_webhook():
    """Mercado Pago llama aquí cuando cambia el estado de un pago.
    IMPORTANTE: para que Mercado Pago pueda llegar a esta URL, el sitio debe
    estar publicado en una dirección accesible desde internet (no funciona
    con localhost). Mientras el sitio esté solo en tu computador, este
    webhook no se va a disparar solo; en ese caso emite la boleta a mano
    con /api/bsale/document una vez que confirmes el pago."""
    payment_id = request.args.get("id") or request.args.get("data.id")
    topic = request.args.get("topic") or request.args.get("type")

    body = request.get_json(silent=True) or {}
    if not payment_id and isinstance(body.get("data"), dict):
        payment_id = body["data"].get("id")
    if not topic:
        topic = body.get("type")

    if topic != "payment" or not payment_id:
        return jsonify({"ok": True, "ignored": True})

    headers = {"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}
    resp = requests.get(f"{MP_API_BASE}/v1/payments/{payment_id}", headers=headers)
    if resp.status_code != 200:
        return jsonify({"ok": False, "error": "no se pudo verificar el pago"}), 502

    payment = resp.json()
    if payment.get("status") != "approved":
        return jsonify({"ok": True, "status": payment.get("status")})

    order_id = int(payment.get("external_reference"))
    orders = load_orders()
    order = find_order(orders, order_id)
    if not order:
        return jsonify({"ok": False, "error": "pedido no encontrado"}), 404

    if order["status"] == "boleta_emitida":
        return jsonify({"ok": True, "already_processed": True})

    try:
        doc = emitir_boleta(order, payment_amount=payment.get("transaction_amount"))
        order["status"] = "boleta_emitida"
        order["bsaleDocument"] = doc
        order["mercadopagoPaymentId"] = payment_id
        save_orders(orders)
    except Exception as e:
        order["status"] = "pagado_sin_boleta"
        order["bsaleError"] = str(e)
        save_orders(orders)
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True})


@app.route("/api/orders/<int:order_id>", methods=["GET"])
def get_order(order_id):
    """Consulta el estado de un pedido (lo usa checkout-resultado.html)."""
    order = find_order(load_orders(), order_id)
    if not order:
        return jsonify({"error": "pedido no encontrado"}), 404
    return jsonify(order)


@app.route("/api/orders", methods=["GET"])
def list_orders():
    """Lista simple de pedidos para revisión manual (uso interno)."""
    return jsonify(load_orders())


_stock_cache = {}  # sku -> (timestamp, quantityAvailable)
STOCK_CACHE_TTL = 60  # segundos


@app.route("/api/stock/<sku>", methods=["GET"])
def stock_lookup(sku):
    """Consulta el stock disponible real en Bsale para un SKU, en vivo
    (con un cache corto para no saturar la API si varias personas miran
    el mismo producto al mismo tiempo)."""
    if not BSALE_API_TOKEN:
        return jsonify({"error": "Bsale no configurado"}), 500

    now = time.time()
    cached = _stock_cache.get(sku)
    if cached and now - cached[0] < STOCK_CACHE_TTL:
        return jsonify({"sku": sku, "stock": cached[1], "cached": True})

    headers = {"access_token": BSALE_API_TOKEN}
    try:
        v_resp = requests.get(f"{BSALE_API_BASE}/variants.json", headers=headers, params={"code": sku})
        v_resp.raise_for_status()
        v_items = v_resp.json().get("items", [])
        if not v_items:
            return jsonify({"sku": sku, "stock": None, "error": "sku no encontrado en Bsale"}), 404
        variant_id = v_items[0]["id"]

        s_resp = requests.get(
            f"{BSALE_API_BASE}/stocks.json",
            headers=headers,
            params={"variantid": variant_id, "officeid": BSALE_OFFICE_ID},
        )
        s_resp.raise_for_status()
        s_items = s_resp.json().get("items", [])
        quantity = s_items[0]["quantityAvailable"] if s_items else 0
    except requests.HTTPError as e:
        return jsonify({"error": "bsale_error", "detail": e.response.text}), 502

    _stock_cache[sku] = (now, quantity)
    return jsonify({"sku": sku, "stock": quantity, "cached": False})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
