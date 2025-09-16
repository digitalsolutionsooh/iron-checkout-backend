from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from decimal import Decimal
from fastapi import APIRouter
import os
import re
import stripe
import time
import hashlib
import requests
import urllib.parse
import hmac, base64
import json
import uuid

def add_sid(url: str) -> str:
    sep = '&' if '?' in url else '?'
    return f"{url}{sep}sid={{CHECKOUT_SESSION_ID}}"

app = FastAPI()

# CORS
origins = origins = [
    "https://learnmoredigitalcourse.com",
    "https://burnjaroformula.online",
    "https://yt2025hub.com",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"^https://(.+\.)?(converteai\.net|converteai\.com\.br|vturb\.com)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Env vars
STRIPE_SECRET_KEY   = os.getenv("STRIPE_SECRET_KEY")
WEBHOOK_SECRET      = os.getenv("STRIPE_WEBHOOK_SECRET")
PIXEL_ID            = os.getenv("PIXEL_ID")
ACCESS_TOKEN        = os.getenv("ACCESS_TOKEN")
UTMIFY_API_URL      = os.getenv("UTMIFY_API_URL")
UTMIFY_API_KEY      = os.getenv("UTMIFY_API_KEY")

@app.get("/health")
async def health():
    return {"status": "up"}

@app.post("/ping")
async def ping():
    return {"pong": True}

@app.post("/create-checkout-session")
async def create_checkout_session(request: Request):
    stripe.api_key = STRIPE_SECRET_KEY

    body = await request.json()
    price_id = body.get("price_id")
    quantity = body.get("quantity", 1)
    customer_email = body.get("customer_email")
    # coletamos os UTMs
    utms = { k: body.get(k, "") for k in (
        "utm_source", 
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content"
    ) }

    if not price_id:
        return JSONResponse(status_code=400, content={"error": "price_id is required"})

    # escolhe a URL de sucesso de acordo com o produto
    if price_id in (
        'price_1S6eVdEn1uVju5MMBIHounGM',
        'price_1S6eVqEn1uVju5MMVjiyAaER'
    ):
        success_url = add_sid('https://learnmoredigitalcourse.com/iron-stripe-up1')
    else:
        success_url = add_sid('https://learnmoredigitalcourse.com/iron-stripe-9')

    session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[{'price': price_id, 'quantity': quantity}],
        mode='payment',
        customer_creation='always',
        customer_email=customer_email,
        phone_number_collection={"enabled": True},
        billing_address_collection="required",
        success_url=success_url,
        cancel_url='https://learnmoredigitalcourse.com/erro',
        # grava UTMs na própria Session
        metadata=utms,
        # grava UTMs também no PaymentIntent
        payment_intent_data={
            "metadata": utms,
            "setup_future_usage": "off_session"
        },
        expand=["line_items"]
    )

    # Conversions API: InitiateCheckout
    event_payload = {
      "data": [{
        "event_name":    "InitiateCheckout",
        "event_time":    int(time.time()),
        "event_id":      session.id,
        "action_source": "website",
        "event_source_url": str(request.url),
        "user_data": {
          "client_ip_address": request.client.host,
          "client_user_agent": request.headers.get("user-agent")
        },
        "custom_data": {
          "currency": session.currency,
          "value":    session.amount_total / 100.0,
          "content_ids": [item.price.id for item in session.line_items.data],
          "content_type": "product"
        }
      }]
    }
    # envia e loga o response para debug
    resp = requests.post(
      f"https://graph.facebook.com/v14.0/{PIXEL_ID}/events",
      params={"access_token": ACCESS_TOKEN},
      json=event_payload
    )
    print("→ InitiateCheckout event sent:", resp.status_code, resp.text)

    # ──────────────────────────────────────────────────
    #  Envia pedido (order) ao UTMify
    cd = session.customer_details or {}
    customer_name  = getattr(cd, "name", "") or ""
    customer_email = getattr(cd, "email", "") or ""
    customer_phone = getattr(cd, "phone", None)
    
    utmify_order = {
      "orderId":       session.id,
      "platform":      "Stripe",
      "paymentMethod": "credit_card",
      "status":        "waiting_payment",
      "createdAt":     time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
      "approvedDate":  None,
      "refundedAt":    None,
      "customer": {
          "name":     customer_name,
          "email":    customer_email,
          "phone":    customer_phone,
          "document": None
      },
      "products": [
        {
          "id":            item.price.id,
          "name":          item.description or item.price.id,
          "planId":        item.price.id,
          "planName":      item.price.nickname or "",
          "quantity":      item.quantity,
          "priceInCents":  item.amount_subtotal
        }
        for item in session.line_items.data
      ],
      "trackingParameters": {
        "utm_source":       session.metadata.get("utm_source",""),
        "utm_medium":       session.metadata.get("utm_medium",""),
        "utm_campaign":     session.metadata.get("utm_campaign",""),
        "utm_term":         session.metadata.get("utm_term",""),
        "utm_content":      session.metadata.get("utm_content","")
      },
      "commission": {
        "totalPriceInCents":     session.amount_total,
        "gatewayFeeInCents":     0,
        "userCommissionInCents": 0,
        "currency":              session.currency.upper()
      }
    }
    resp_utm = requests.post(
      UTMIFY_API_URL,
      headers={
        "Content-Type": "application/json",
        "x-api-token":  UTMIFY_API_KEY
      },
      json=utmify_order
    )
    print("→ Order enviado ao UTMify:", resp_utm.status_code, resp_utm.text)
    # ──────────────────────────────────────────────────

    return {
        "checkout_url": session.url,
        "session_id": session.id,  # usaremos como eventID do Pixel
    }

@app.post("/upsell/intent")
async def create_upsell_intent(request: Request):
    stripe.api_key = STRIPE_SECRET_KEY
    body = await request.json()
    sid      = body.get("sid")
    price_id = body.get("price_id")
    quantity = int(body.get("quantity", 1))

    if not sid or not price_id:
        return JSONResponse(status_code=400, content={"error": "sid and price_id are required"})

    # 1) Recupera a Session anterior e extrai customer + payment_method
    sess = stripe.checkout.Session.retrieve(
        sid,
        expand=["payment_intent.payment_method", "customer"]
    )
    if not sess or not sess.customer:
        return JSONResponse(status_code=400, content={"error": "Invalid session or missing customer"})
    
    customer_id = sess.customer

    # preferimos o PM da PI da Session
    pm = getattr(getattr(sess, "payment_intent", None), "payment_method", None)
    pm_id = pm.id if pm else None

    # fallback: default do customer
    if not pm_id and getattr(sess, "customer", None):
        cust = sess.customer if isinstance(sess.customer, dict) else stripe.Customer.retrieve(customer_id)
        pm_id = (cust.get("invoice_settings", {}) or {}).get("default_payment_method")

    if not pm_id:
        # Sem método salvo? devolve erro orientando a abrir um novo Checkout
        return JSONResponse(status_code=409, content={"error": "No saved payment method; redirect to checkout"})

    # 2) Carrega o price para pegar valor/moeda/identificação
    price = stripe.Price.retrieve(price_id)
    amount_minor = price["unit_amount"] * quantity
    currency = price["currency"]

    # 3) Metadados: copie UTMs da Session anterior e marque como upsell
    base_meta = dict(sess.metadata or {})
    base_meta.update({
        "upsell": "true",
        "parent_session": sid,
        "price_id": price_id,
        "quantity": str(quantity),
    })

    # 4) Idempotência p/ evitar dupla cobrança por duplo clique
    idem_key = f"upsell:{sid}:{price_id}:{quantity}"

    intent = stripe.PaymentIntent.create(
        amount=amount_minor,
        currency=currency,
        customer=customer_id,
        payment_method=pm_id,
        confirmation_method="automatic",   # confirmaremos no front
        metadata=base_meta,
        idempotency_key=idem_key,
        payment_method_options={
            "card": {
                "request_three_d_secure": "any"  # Tenta 3D Secure se o cartão suportar
            }
        }
    )

    return {"client_secret": intent.client_secret, "pm_id": pm_id}

@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig     = request.headers.get("stripe-signature", "")

    # 1) Garante que podemos chamar a API do Stripe
    stripe.api_key = STRIPE_SECRET_KEY

    # 2) Valida a assinatura do webhook
    try:
        event = stripe.Webhook.construct_event(payload, sig, WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError as e:
        print("⚠️ Webhook signature mismatch:", e)
        raise HTTPException(400, "Invalid webhook signature")

    # 3) Se for checkout.session.completed, processa
    if event["type"] == "checkout.session.completed":
        session = stripe.checkout.Session.retrieve(
            event["data"]["object"]["id"],
            expand=["line_items"]
        )
        # captura o createdAt original a partir do timestamp da session:
        original_created_at = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(session.created))
        cust = session["customer"]

        # 3.1) Primeiro, guarda as UTMs no Customer
        stripe.Customer.modify(
            cust, 
            metadata=session.metadata,
            name=session.customer_details.name,
            phone=session.customer_details.phone
        )

        # 3.2) Prepara o payload de Purchase para o Meta
        email_hash = hashlib.sha256(
            session.customer_details.email.encode("utf-8")
        ).hexdigest()
        purchase_payload = {
            "data": [{
                "event_name":    "Purchase",
                "event_time":    int(time.time()),
                "event_id":      session.id,
                "action_source": "website",
                "event_source_url": session.url,
                "user_data":     {"em": email_hash},
                "custom_data":   {
                    "currency":     session.currency,
                    "value":        session.amount_total / 100.0,
                    "content_ids":  [li.price.id for li in session.line_items.data],
                    "content_type": "product"
                }
            }]
        }

        # 3.3) Gera/reativa a Invoice espelho (sem "Payment for Invoice (canceled)")
        def clean_desc(raw: str) -> str:
            return re.sub(r"\s*\(Session\s+cs_[a-zA-Z0-9_]+\)\s*$", "", (raw or "")).strip()
        
        try:
            print(f"🔔 [webhook] criando/finalizando invoice (safe) para sessão {session.id}")
            idem_prefix = f"cs:{session.id}"
        
            footer_text = (
                "Thank you for purchasing the formula. To access the material, "
                "simply click on the link and follow the instructions: "
                "https://learnmoredigitalcourse.com/iron-members-area/\n\n"
                "If you have any questions, please send an email to: "
                "digital.solutions.ooh@gmail.com"
            )
        
            # 0) Procura invoice já associada a esta sessão
            invoice = None
            try:
                invs = stripe.Invoice.list(customer=cust, limit=50)
                for it in invs.auto_paging_iter():
                    if (it.get("metadata") or {}).get("parent_session_id") == session.id:
                        invoice = it
                        break
            except Exception as _:
                pass
        
            if not invoice:
                # 1) Carrega line items do Checkout e define a moeda
                line_items = stripe.checkout.Session.list_line_items(
                    session.id,
                    expand=["data.price.product", "data.price"]
                )
                checkout_currency = (getattr(session, "currency", None) or "usd").lower()
        
                first_li = None
                for li in line_items.auto_paging_iter():
                    first_li = li
                    break
                if first_li is None:
                    raise RuntimeError("Checkout sem line items; nada para faturar.")
        
                currency = (first_li.get("currency")
                            or ((first_li.get("price") or {}).get("currency"))
                            or checkout_currency).lower()
        
                # 2) Limpa PENDENTES de outras sessões (mantém os desta sessão)
                pending_items = stripe.InvoiceItem.list(customer=cust, limit=100)
                for ii in pending_items.auto_paging_iter():
                    if ii.get("invoice") is None and (ii.get("metadata") or {}).get("parent_session_id") != session.id:
                        try:
                            stripe.InvoiceItem.delete(ii.id)
                            print(f"   → Pending antigo removido: {ii.id}")
                        except Exception:
                            pass
        
                # 3) Cria InvoiceItems pendentes para cada line item do Checkout
                created_any = False
                for li in stripe.checkout.Session.list_line_items(
                        session.id, expand=["data.price.product", "data.price"]).auto_paging_iter():
        
                    total = li.get("amount_total") or li.get("amount_subtotal")
                    if total is None:
                        price = (li.get("price") or {})
                        unit = int(price.get("unit_amount") or 0)
                        qty  = int(li.get("quantity") or 1)
                        total = unit * qty
        
                    price   = li.get("price") or {}
                    product = price.get("product") or {}
                    name = product.get("name") or price.get("nickname") or li.get("description") or "Item"
        
                    stripe.InvoiceItem.create(
                        customer=cust,
                        currency=currency,
                        amount=int(total),
                        description=clean_desc(name),
                        metadata={
                            "source": "mirror_checkout_session",
                            "parent_session_id": session.id,
                            "line_item_id": li.get("id", "")
                        },
                        idempotency_key=f"{idem_prefix}:ii:{li.get('id')}",
                    )
                    created_any = True
                    print(f"   → InvoiceItem pendente criado | total: {int(total)/100:.2f} {currency.upper()}")
        
                if not created_any:
                    raise RuntimeError("Nenhum InvoiceItem criado; verifique os line items.")
        
                # 4) Cria a Invoice incluindo os pendentes
                invoice = stripe.Invoice.create(
                    customer=cust,
                    collection_method="send_invoice",
                    days_until_due=30,
                    pending_invoice_items_behavior="include",
                    auto_advance=False,
                    description="Compra via Checkout",
                    footer=footer_text,
                    metadata={**(dict(session.metadata or {})), "parent_session_id": session.id},
                )
                print(f"   → Invoice draft criada: {invoice.id} | currency={invoice.currency.upper()}")
            else:
                print(f"   → Reutilizando invoice existente: {invoice.id} (status={invoice.status})")
                # Se ainda draft, garante os campos/rodapé
                if invoice.status == "draft":
                    invoice = stripe.Invoice.modify(
                        invoice.id,
                        collection_method="send_invoice",
                        days_until_due=30,
                        description="Compra via Checkout",
                        footer=footer_text,
                    )
        
            # 5) Finaliza (se necessário) e paga OOB, com idem por INVOICE (não por sessão)
            if invoice.status == "draft":
                invoice = stripe.Invoice.finalize_invoice(
                    invoice.id,
                    auto_advance=False,
                    idempotency_key=f"invoice:{invoice.id}:finalize",
                )
            print(f"   → Invoice finalizada: {invoice.id} | amount_due: {invoice.amount_due/100:.2f} {invoice.currency.upper()}")
        
            # garante collection_method
            if invoice.collection_method != "send_invoice":
                stripe.Invoice.modify(
                    invoice.id,
                    collection_method="send_invoice",
                    due_date=invoice.due_date or int(time.time()) + 30*24*60*60
                )
        
            state = stripe.Invoice.retrieve(invoice.id, expand=["payment_intent"])
            pi_obj = getattr(state, "payment_intent", None)
            print(f"   → collection_method={state.collection_method} | has_pi={bool(pi_obj)}")
            if pi_obj:
                print(f"   → PI inesperado atrelado: {getattr(pi_obj, 'id', pi_obj)}")
        
            if invoice.status != "paid":
                paid = stripe.Invoice.pay(
                    invoice.id,
                    paid_out_of_band=True,
                    idempotency_key=f"invoice:{invoice.id}:pay",
                )
                print(f"   → Invoice marcada como PAGA: {paid.id} | amount_paid: {paid.amount_paid/100:.2f} {paid.currency.upper()}")
                print(f"   → Links: hosted={paid.hosted_invoice_url} | pdf={paid.invoice_pdf}")
            else:
                print("   → Invoice já estava 'paid'.")
        
        except Exception as e:
            import traceback
            print("‼️ Erro criando/finalizando invoice:", e)
            print(traceback.format_exc())


        finally:
            # 4) Mesmo se der erro acima, sempre envia o evento Purchase
            resp = requests.post(
                f"https://graph.facebook.com/v14.0/{PIXEL_ID}/events",
                params={"access_token": ACCESS_TOKEN},
                json=purchase_payload
            )
            print("→ Purchase event sent:", resp.status_code, resp.text)

            # 4.1) Atualiza todo o order como "paid" — POST full payload
            total = session.amount_total
            fee   = total * Decimal("0.0674")   
            net   = total - fee      
            
            utmify_order_paid = {
              "orderId":       session.id,
              "platform":      "Stripe",
              "paymentMethod": "credit_card",
              "status":        "paid",
              "createdAt":     original_created_at,   # timestamp que você calculou lá em cima
              "approvedDate":  time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
              "refundedAt":    None,
              "customer": {
                "name":     session.customer_details.name  or "",
                "email":    session.customer_details.email,
                "phone":    session.customer_details.phone or None,
                "document": None
              },
              "products": [
                {
                  "id":            li.price.id,
                  "name":          li.description or li.price.id,
                  "planId":        li.price.id,
                  "planName":      li.price.nickname or None,
                  "quantity":      li.quantity,
                  "priceInCents":  li.amount_subtotal
                }
                for li in session.line_items.data
              ],
              "trackingParameters": {
                "utm_source":     session.metadata.get("utm_source",""),
                "utm_medium":     session.metadata.get("utm_medium",""),
                "utm_campaign":   session.metadata.get("utm_campaign",""),
                "utm_term":       session.metadata.get("utm_term",""),
                "utm_content":    session.metadata.get("utm_content","")
              },
             "commission": {
                "totalPriceInCents":     float(total),  
                "gatewayFeeInCents":     float(fee),
                "userCommissionInCents": float(net),
                "currency":              session.currency.upper()
             }
            }
            
            resp_utm = requests.post(
              UTMIFY_API_URL,
              headers={
                "Content-Type": "application/json",
                "x-api-token":  UTMIFY_API_KEY
              },
              json=utmify_order_paid
            )
            print("→ Pedido atualizado como pago na UTMify:", resp_utm.status_code, resp_utm.text)

    elif event["type"] == "payment_intent.succeeded":
        # ↳ UPSSELL 1-CLICK (confirmado no front com confirmCardPayment)
        intent_id = event["data"]["object"]["id"]
        intent = stripe.PaymentIntent.retrieve(intent_id, expand=["latest_charge"])
    
        # Só processa se marcamos como upsell no metadata
        meta = dict(getattr(intent, "metadata", {}) or {})
        if meta.get("upsell") != "true":
            # não é upsell, ignorar
            return JSONResponse({"received": True})
    
        # ── Nomes do produto/price na Stripe (para UTMify) ─────────────────────
        product_name = "Upsell"   # fallback
        plan_name    = "Upsell"   # fallback
        product_id   = None
    
        price_id = meta.get("price_id")
        if price_id:
            try:
                pr = stripe.Price.retrieve(price_id, expand=["product"])
                # apelido do price (se houver)
                plan_name = getattr(pr, "nickname", None) or plan_name
    
                prod_obj = getattr(pr, "product", None)
                # StripeObject costuma ter .get(); se vier id string, busca o produto
                if isinstance(prod_obj, dict) or hasattr(prod_obj, "get"):
                    product_name = prod_obj.get("name") or plan_name or product_name
                    product_id   = prod_obj.get("id")
                elif isinstance(prod_obj, str):
                    prod = stripe.Product.retrieve(prod_obj)
                    product_name = getattr(prod, "name", None) or plan_name or product_name
                    product_id   = getattr(prod, "id", None)
            except Exception as e:
                print("→ Falha ao obter nome do upsell:", e)
        # ───────────────────────────────────────────────────────────────────────
    
        # ── Dados do cliente (name/email/phone) ──────────────────────────
        email = name = phone = None
    
        # 1) billing_details da primeira charge
        ch = getattr(intent, "charges", None)
        if ch and getattr(ch, "data", None):
            c0 = ch.data[0]
            bd = getattr(c0, "billing_details", None)
            if bd:
                email = getattr(bd, "email", None) or None
                name  = getattr(bd, "name",  None) or None
                phone = getattr(bd, "phone", None) or None
    
        if (not email or not name or not phone) and getattr(intent, "latest_charge", None):
            bd = getattr(intent.latest_charge, "billing_details", None)
            if bd:
                email = getattr(bd, "email", None) or email
                name  = getattr(bd, "name",  None) or name
                phone = getattr(bd, "phone", None) or phone
    
        # 2) fallback: Customer
        cust_id = getattr(intent, "customer", None)
        if cust_id and (not email or not name or not phone):
            cust = stripe.Customer.retrieve(cust_id)
            email = email or (cust.get("email") or None)
            name  = name  or (cust.get("name")  or None)
            phone = phone or (cust.get("phone") or None)
    
        # ── Cálculos ─────────────────────────────────────────────────────
        total = int(intent.amount)                         # em centavos
        fee   = total * Decimal("0.0674")
        net   = total - fee

        # ── CAPI Purchase (email hash se disponível) ────────────────────
        email_hash = hashlib.sha256(email.encode("utf-8")).hexdigest() if email else None
        purchase_payload = {
            "data": [{
                "event_name": "Purchase",
                "event_time": int(time.time()),
                "event_id":   intent.id,
                "action_source": "website",
                "user_data": ({"em": email_hash} if email_hash else {}),
                "custom_data": {
                    "currency": intent.currency,
                    "value":    total / 100.0,
                    "content_ids":  [meta.get("price_id")] if meta.get("price_id") else [],
                    "content_type": "product"
                }
            }]
        }
        try:
            requests.post(
                f"https://graph.facebook.com/v14.0/{PIXEL_ID}/events",
                params={"access_token": ACCESS_TOKEN},
                json=purchase_payload
            )
        except Exception as e:
            print("→ CAPI (upsell) erro:", e)

        # ── UTMify paid (mantendo campos e comissão como no principal) ──
        utmify_order_paid = {
          "orderId":       intent.id,
          "platform":      "Stripe",
          "paymentMethod": "credit_card",
          "status":        "paid",
          "createdAt":     time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(intent.created)),
          "approvedDate":  time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
          "refundedAt":    None,
          "customer": {
            "name":     name  or "",
            "email":    email or "",
            "phone":    phone or None,
            "document": None
          },
          "products": [
              {
                "id":           product_id or price_id,   # agrupar por produto? prefira product_id
                "name":         product_name,             # nome real do produto (Stripe)
                "planId":       price_id,                 # mantém o Price como plano
                "planName":     plan_name,                # nickname do Price (ou fallback)
                "quantity":     int(meta.get("quantity","1") or "1"),
                "priceInCents": total
              }
            ],
          "trackingParameters": {
            "utm_source":   meta.get("utm_source",""),
            "utm_medium":   meta.get("utm_medium",""),
            "utm_campaign": meta.get("utm_campaign",""),
            "utm_term":     meta.get("utm_term",""),
            "utm_content":  meta.get("utm_content","")
          },
          "commission": {
            "totalPriceInCents":     float(total),
            "gatewayFeeInCents":     float(fee),
            "userCommissionInCents": float(net),
            "currency":              intent.currency.upper()
          }
        }

        try:
            resp_utm = requests.post(
              UTMIFY_API_URL,
              headers={"Content-Type": "application/json","x-api-token": UTMIFY_API_KEY},
              json=utmify_order_paid
            )
            print("→ Upsell pago enviado ao UTMify:", resp_utm.status_code, resp_utm.text)
        except Exception as e:
            print("→ UTMify (upsell) erro:", e)
    
    # 5) Retorna 200 sempre
    return JSONResponse({"received": True})

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
