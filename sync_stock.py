"""
Sincroniza el stock real de Bsale hacia data/products.json.

Qué hace:
  1. Descarga todas las variantes de Bsale (id + code/SKU).
  2. Descarga el stock disponible de cada variante en la sucursal configurada.
  3. Cruza ambos por "code" (que es el mismo valor que usamos como SKU) y
     escribe un campo "stock" en cada producto de data/products.json.

Correrlo:
    pip install -r backend/requirements.txt
    python backend/sync_stock.py

Se puede correr manualmente cada vez que quieras refrescar el stock, o
programarlo con el Programador de tareas de Windows para que corra solo
cada cierto tiempo.
"""
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

BSALE_API_TOKEN = os.environ.get("BSALE_API_TOKEN", "")
BSALE_OFFICE_ID = os.environ.get("BSALE_OFFICE_ID", "1")
BSALE_API_BASE = "https://api.bsale.io/v1"
HEADERS = {"access_token": BSALE_API_TOKEN}

PRODUCTS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "products.json")


def fetch_all(path, params=None):
    """Pagina un endpoint de Bsale y devuelve todos los items."""
    items = []
    offset = 0
    limit = 50
    params = dict(params or {})
    while True:
        params.update({"limit": limit, "offset": offset})
        resp = requests.get(f"{BSALE_API_BASE}/{path}", headers=HEADERS, params=params)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data["items"])
        if len(data["items"]) < limit:
            break
        offset += limit
    return items


def main():
    if not BSALE_API_TOKEN:
        raise SystemExit("Falta configurar BSALE_API_TOKEN en backend/.env")

    print("Descargando variantes desde Bsale...")
    variants = fetch_all("variants.json")
    variant_id_to_code = {}
    for v in variants:
        if v.get("code"):
            variant_id_to_code[str(v["id"])] = v["code"]
    print(f"  {len(variant_id_to_code)} variantes con código.")

    print("Descargando stock desde Bsale...")
    stocks = fetch_all("stocks.json", params={"officeid": BSALE_OFFICE_ID})
    code_to_stock = {}
    for s in stocks:
        variant_id = s["variant"]["id"]
        code = variant_id_to_code.get(str(variant_id))
        if code:
            code_to_stock[code] = s.get("quantityAvailable", 0)
    print(f"  {len(code_to_stock)} stocks encontrados.")

    print("Actualizando data/products.json...")
    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        products = json.load(f)

    matched = 0
    for p in products:
        sku = p.get("sku")
        if sku and sku in code_to_stock:
            p["stock"] = code_to_stock[sku]
            matched += 1
        else:
            p["stock"] = None  # no encontrado en Bsale: se desconoce el stock

    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=0)

    print(f"Listo. {matched}/{len(products)} productos actualizados con stock real de Bsale.")


if __name__ == "__main__":
    main()
