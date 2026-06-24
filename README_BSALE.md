# Pagos (Mercado Pago) + boletas (Bsale) — cómo está conectado

El cobro real y la emisión de boleta **ya están conectados** en `backend/app.py`:

1. El cliente completa `checkout.html` y hace clic en "Pagar con Mercado Pago".
2. El frontend llama a `POST /api/checkout` → el backend registra el pedido y
   crea una **preferencia de pago en Mercado Pago** (`crear_preferencia_mercadopago`),
   devolviendo un `init_point` (link de pago real).
3. El frontend redirige al cliente a ese link para que pague con tarjeta/transferencia.
4. Cuando el pago se aprueba, Mercado Pago avisa a `POST /api/mercadopago/webhook`,
   que verifica el pago y llama a `emitir_boleta(order, ...)` → se crea la
   boleta electrónica real en Bsale, con el medio de pago "Mercado Pago".
5. El cliente vuelve a `checkout-resultado.html`, que muestra si el pago y la
   boleta quedaron OK.

## Variables de entorno (`backend/.env`)

```
BSALE_API_TOKEN=...
BSALE_OFFICE_ID=1
BSALE_DOCUMENT_TYPE_ID=1
BSALE_IVA_TAX_ID=1
BSALE_MP_PAYMENT_TYPE_ID=13

MP_PUBLIC_KEY=...
MP_ACCESS_TOKEN=...

SITE_BASE_URL=http://localhost:8000
BACKEND_BASE_URL=http://localhost:5000
```

**Nunca subas este archivo a un repositorio público.**

## ⚠️ Importante: esto funciona completo solo cuando el sitio esté publicado

Mientras el sitio corra en tu computador (`localhost`):

- **Sí funciona**: crear el pedido, generar el link de pago de Mercado Pago y
  que el cliente pague.
- **No funciona automáticamente**: el aviso de "pago aprobado" (webhook) y la
  emisión automática de la boleta — Mercado Pago no puede mandarle una
  notificación a tu computador desde internet porque `localhost` no es una
  dirección pública.
- En ese caso, después de que el cliente te avise que pagó (por WhatsApp, por
  ejemplo), puedes emitir la boleta a mano llamando a:
  `POST http://localhost:5000/api/bsale/document` con `{"order_id": <id>}`.

**Cuando publiques el sitio en un dominio real** (con hosting), tienes que:

1. Cambiar en `backend/.env`:
   - `SITE_BASE_URL` a tu dominio real, ej. `https://torqexferreteria.cl`
   - `BACKEND_BASE_URL` a donde quede corriendo el backend, ej. `https://api.torqexferreteria.cl`
2. Con eso, `auto_return` y `notification_url` se activan solos (el código ya
   los omite si detecta `localhost`), y el flujo completo —pago → boleta
   automática— queda 100% funcionando sin intervención manual.

## Revisar pedidos

`backend/orders.json` guarda todos los pedidos (estado `pendiente_pago`,
`boleta_emitida`, etc.) y `GET /api/orders` los lista. `GET /api/orders/<id>`
da el detalle de uno solo.

## Sobre el medio de pago "Mercado Pago" en Bsale

En tu cuenta de Bsale ya existe el método de pago "Mercado Pago" (id `13`,
ver `BSALE_MP_PAYMENT_TYPE_ID`). Es solo una etiqueta contable: cuando se
emite la boleta tras un pago aprobado, queda registrada con ese medio de
pago para que tu contabilidad en Bsale quede ordenada. El cobro real lo
sigue haciendo Mercado Pago, Bsale solo factura.
