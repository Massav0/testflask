import os
import io
import json
import hmac
import base64
import hashlib
from datetime import datetime
from typing import Optional, Dict, Any

import requests
from flask import Flask, request, send_file, jsonify, render_template_string, abort, redirect
from flask_cors import CORS

import qrcode
import qrcode.image.svg as qrcode_svg

app = Flask(__name__)

# ───────────────────────── Config / ENV ─────────────────────────
# ENV: "live" | "sandbox"
FEDAPAY_ENV = os.getenv("FEDAPAY_ENV", "live").strip().lower()
EVENT_PRICE_XOF = int(os.getenv("EVENT_PRICE_XOF", "100"))
EVENT_CURRENCY = os.getenv("EVENT_CURRENCY", "XOF").upper()

# Clé d'API FedaPay (SECRÈTE) pour créer les transactions côté serveur
FEDAPAY_SECRET_KEY = os.getenv("FEDAPAY_SECRET_KEY", "").strip()

# Webhook secret (fourni dans Dashboard > Webhooks)
FEDAPAY_WEBHOOK_SECRET = os.getenv("FEDAPAY_WEBHOOK_SECRET", "change-me-webhook-secret").strip()

# Clé de signature des QR (obligatoire en prod)
QR_SIGNING_KEY = os.getenv("QR_SIGNING_KEY", "change-me-signing-key").encode()

# API base selon env
FEDAPAY_API_BASE = os.getenv(
    "FEDAPAY_API_BASE",
    ""https://api.fedapay.com/v1"
).rstrip("/")

# URL de retour (front) après paiement — configure ton domaine ici
CALLBACK_URL = os.getenv("CALLBACK_URL", "https://ton-front.com/retour")

# Autoriser CORS pour les endpoints API (utile si front statique sur un autre domaine)
CORS(app, resources={r"/api/*": {"origins": "*"}, r"/pay-intent": {"origins": "*"}})

# ───────────────────────── “Mini-DB” en mémoire (exemple) ─────────────────────────
# Remplace par une vraie base (SQL/NoSQL). Clé = txid (id transaction FedaPay).
TX_STORE: Dict[str, Dict[str, Any]] = {}
# Valeurs typiques :
# {
#   txid: {
#       "status": "pending"|"paid"|"failed",
#       "amount": 3000, "currency": "XOF",
#       "nom": "...", "prenom": "...", "email": "...",
#       "qr_png_b64": "data:image/png;base64,...",  # si paid
#       "ts": "2025-11-07T12:00:00Z",
#       "sig": "..."   # signature du QR
#   }
# }

# ───────────────────────── Utilitaires QR ─────────────────────────
MAX_LEN = 280  # limite simple pour éviter les abus

def build_payload(nom: str, prenom: str, txid: Optional[str]) -> str:
    """
    Construit le JSON encodé dans le QR et le signe (HMAC-SHA256).
    Signature couvre: nom|prenom|txid|ts
    En LIVE, txid est requis.
    """
    nom = (nom or "").strip()
    prenom = (prenom or "").strip()
    txid = (txid or "").strip()

    if not nom or not prenom:
        abort(400, "Champs 'nom' et 'prenom' requis")
    if len(nom) > MAX_LEN or len(prenom) > MAX_LEN:
        abort(413, "Champs trop longs (max 280 caractères)")

    if FEDAPAY_ENV == "live" and not txid:
        abort(400, "Champ 'txid' requis (id de transaction FedaPay)")

    ts = datetime.utcnow().isoformat() + "Z"
    msg = "|".join([nom, prenom, txid, ts])
    sig = hmac.new(QR_SIGNING_KEY, msg.encode(), hashlib.sha256).hexdigest()

    return json.dumps(
        {"nom": nom, "prenom": prenom, "txid": txid, "ts": ts, "sig": sig, "alg": "HS256", "kid": "qr_v1"},
        ensure_ascii=False
    )

def make_qr_png(data: str) -> bytes:
    """Génère un PNG en mémoire."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def make_qr_svg(data: str) -> bytes:
    """Génère un SVG minifié."""
    factory = qrcode_svg.SvgPathImage
    img = qrcode.make(data, image_factory=factory, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()

def png_bytes_to_data_url(png_bytes: bytes) -> str:
    b64 = base64.b64encode(png_bytes).decode()
    return f"data:image/png;base64,{b64}"

# ───────────────────────── Pages de test (facultatif) ─────────────────────────
INDEX = """
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Timeline Paiement → Webhook → QR</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html,body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
    main{max-width:800px;margin:2rem auto;padding:0 1rem;line-height:1.6}
    form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.5rem}
    input,button{padding:.6rem .8rem}
    img{max-width:280px;border:1px solid #eee;border-radius:.5rem;padding:.5rem;background:#fff}
    .mono{font-family:ui-monospace,Consolas,monospace}
  </style>
</head>
<body>
<main>
  <h1>Démo timeline : Front → Back → FedaPay → Webhook → QR</h1>
  <p>1) Renseigne tes infos et clique <strong>Payer</strong>. Le back crée une transaction FedaPay et te renvoie <code>pay_url</code> (ici on redirige).</p>
  <form id="f">
    <input name="nom" placeholder="Nom" required>
    <input name="prenom" placeholder="Prénom" required>
    <input name="email" placeholder="Email (pour envoi)">
    <button type="submit">Payer</button>
  </form>
  <p class="mono">CALLBACK_URL (retour après paiement) : {{cb}}</p>
  <hr>
  <p><em>Après le paiement</em>, FedaPay redirige vers <code>CALLBACK_URL?id=...&status=...</code> (front) et envoie aussi le <strong>webhook</strong> au back. La page de retour appelle <code>/api/tx-status?id=...</code> jusqu’à ce que le back dise <code>paid</code> et retourne le QR.</p>
</main>
</body>
</html>
"""

@app.get("/")
def index():
    return render_template_string(INDEX, cb=CALLBACK_URL)

# ───────────────────────── Endpoints “timeline” ─────────────────────────
@app.post("/pay-intent")
def pay_intent():
    """
    FRONT → BACK
    Le front poste {nom, prenom, email?}. Le back crée la transaction FedaPay et renvoie {pay_url, tx_ref?}.
    """
    body = request.get_json(silent=True) or {}
    nom = (body.get("nom") or "").strip()
    prenom = (body.get("prenom") or "").strip()
    email = (body.get("email") or "").strip()

    if not nom or not prenom:
        abort(400, "Champs 'nom' et 'prenom' requis")

    # Préparer la création de transaction côté FedaPay
    # NOTE: la structure exacte peut varier selon l'API/SDK utilisé.
    # On prévoit les champs usuels: amount, currency, description, callback_url, metadata.
    payload = {
        "description": f"Billet {nom} {prenom}",
        "amount": EVENT_PRICE_XOF,
        "currency": EVENT_CURRENCY,
        "callback_url": CALLBACK_URL,
        "metadata": {
            "nom": nom,
            "prenom": prenom,
            "email": email,
        }
    }

    # Auth basique: clé secrète en Basic-Auth (username = key, password = "")
    # Tu peux aussi utiliser Authorization: Bearer <key> si ton intégration le demande.
    headers = {"Content-Type": "application/json"}
    auth = (FEDAPAY_SECRET_KEY, "")

    try:
        r = requests.post(f"{FEDAPAY_API_BASE}/transactions", json=payload, headers=headers, auth=auth, timeout=15)
        r.raise_for_status()
        data = r.json() or {}
    except Exception as e:
        app.logger.exception("Erreur création transaction FedaPay")
        abort(502, f"Erreur FedaPay: {e}")

    # Extraire l'URL de paiement et un identifiant
    # (Selon la réponse, l'URL peut s'appeler link/url/checkout_url; on tente plusieurs possibilités)
    pay_url = (
        data.get("data", {}).get("link")
        or data.get("data", {}).get("url")
        or data.get("data", {}).get("checkout_url")
        or data.get("link")
        or data.get("url")
    )
    # Possible identifiant de transaction (txid) renvoyé à la création
    txid = (
        str(data.get("data", {}).get("id") or "")
        or str(data.get("id") or "")
    )

    if not pay_url:
        abort(502, "Réponse FedaPay sans lien de paiement")

    # Optionnel: initialiser en mémoire si txid connu dès maintenant
    if txid:
        TX_STORE[txid] = {
            "status": "pending",
            "amount": EVENT_PRICE_XOF,
            "currency": EVENT_CURRENCY,
            "nom": nom,
            "prenom": prenom,
            "email": email,
        }

    # Le front redirigera vers pay_url
    return jsonify({"pay_url": pay_url, "tx_ref": txid or None})

# Vérification signature webhook (HMAC SHA-256 du body avec secret)
def _verify_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    if not secret or not signature_header:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)

def _extract_currency(tx_currency) -> str:
    if isinstance(tx_currency, dict):
        return (tx_currency.get("iso") or tx_currency.get("code") or "").upper()
    return (tx_currency or "").upper()

def _extract_txid(tx_obj: dict) -> str:
    # Essaye divers champs possibles
    return str(tx_obj.get("id") or tx_obj.get("reference") or tx_obj.get("transaction_id") or "").strip()

@app.post("/webhook/fedapay")
def webhook_fedapay():
    """
    FedaPay → BACK
    Source de vérité. Si transaction approved & montant/devise OK → génère le QR (avec txid signé) et range en DB.
    """
    raw = request.get_data()
    signature = request.headers.get("X-FEDAPAY-SIGNATURE", "")

    if not _verify_signature(raw, signature, FEDAPAY_WEBHOOK_SECRET):
        abort(401, "Signature invalide")

    payload = request.get_json(silent=True) or {}
    event = (payload.get("event") or "").lower()
    data = payload.get("data") or {}
    tx = data.get("object") or {}

    status = (tx.get("status") or "").lower()
    amount = int(tx.get("amount") or 0)
    currency = _extract_currency(tx.get("currency"))

    txid = _extract_txid(tx)
    customer = tx.get("customer") or {}
    prenom = (customer.get("first_name") or "").strip() or (tx.get("metadata", {}) or {}).get("prenom") or "Inconnu"
    nom = (customer.get("last_name") or "").strip() or (tx.get("metadata", {}) or {}).get("nom") or "Inconnu"
    email = (tx.get("metadata", {}) or {}).get("email") or ""

    app.logger.info(f"[Webhook] event={event} status={status} amount={amount} {currency} txid={txid} {nom} {prenom}")

    paid_ok = status in {"approved", "paid", "success", "completed"}
    money_ok = (amount == EVENT_PRICE_XOF and currency in {"XOF", "CFA", "FCFA"})

    # Initialise/complète la fiche en mémoire
    rec = TX_STORE.setdefault(txid or "unknown", {
        "status": "pending",
        "amount": amount,
        "currency": currency,
        "nom": nom, "prenom": prenom, "email": email
    })
    # Mets à jour les infos utiles (au cas où)
    rec.update({"amount": amount or rec.get("amount"), "currency": currency or rec.get("currency"), "nom": nom, "prenom": prenom, "email": email})

    if event == "transaction.approved" and paid_ok and money_ok and txid:
        # Construire le QR avec txid signé
        qr_json = build_payload(nom, prenom, txid)
        png = make_qr_png(qr_json)
        qr_data_url = png_bytes_to_data_url(png)

        # Sauvegarder en “DB”
        obj = json.loads(qr_json)
        rec.update({
            "status": "paid",
            "qr_png_b64": qr_data_url,
            "ts": obj["ts"],
            "sig": obj["sig"]
        })

        app.logger.info(f"[Webhook] ✅ Paiement validé — QR généré (txid={txid})")
        # TODO: envoyer par email si souhaité (attachment png)
    else:
        app.logger.info("[Webhook] ⚠️ Conditions non réunies pour émission du QR")

    return jsonify({"ok": True})

@app.get("/api/tx-status")
def api_tx_status():
    """
    FRONT → BACK (page callback)
    Le front interroge /api/tx-status?id=TXID jusqu'à {status: "paid"}.
    Si paid → renvoie aussi le QR (data URL) pour affichage direct.
    """
    txid = (request.args.get("id") or "").strip()
    if not txid:
        abort(400, "Paramètre 'id' requis")

    rec = TX_STORE.get(txid)
    if not rec:
        return jsonify({"status": "pending"}), 200  # Le webhook peut ne pas avoir encore alimenté

    if rec.get("status") == "paid":
        return jsonify({
            "status": "paid",
            "nom": rec.get("nom"),
            "prenom": rec.get("prenom"),
            "amount": rec.get("amount"),
            "currency": rec.get("currency"),
            "qr_data_url": rec.get("qr_png_b64"),
            "txid": txid,
            "ts": rec.get("ts"),
        })

    return jsonify({"status": rec.get("status", "pending")})

@app.post("/api/verify")
def api_verify():
    """
    Contrôle d'accès à l'événement : vérifier un QR scanné.
    body: {"qr_text": "..."} (contenu texte JSON du QR)
    - vérifie la signature HMAC
    - valide que le txid est 'paid' en DB
    """
    payload = request.get_json(silent=True) or {}
    qr_text = payload.get("qr_text") or ""
    try:
        obj = json.loads(qr_text)
        nom = (obj.get("nom") or "").strip()
        prenom = (obj.get("prenom") or "").strip()
        txid = (obj.get("txid") or "").strip()
        ts = obj.get("ts")
        sig = obj.get("sig") or ""

        if not (nom and prenom and txid and ts and sig):
            abort(400, "Champs manquants dans le QR")

        msg = "|".join([nom, prenom, txid, ts])
        expected = hmac.new(QR_SIGNING_KEY, msg.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            abort(401, "Signature du QR invalide")

        rec = TX_STORE.get(txid)
        if not rec or rec.get("status") != "paid":
            abort(403, "Transaction non reconnue ou non payée")

        # TODO (anti double-scan): marquer 'scanned_at' et refuser si déjà scanné
        return jsonify({"ok": True, "nom": nom, "prenom": prenom, "txid": txid, "ts": ts})
    except Exception:
        abort(400, "QR invalide")

# ───────────────────────── Utilitaires & endpoints hérités ─────────────────────────
@app.get("/qr")
def preview_qr():
    """GET /qr?text=... → renvoie un PNG rapide (debug uniquement)."""
    text = (request.args.get("text") or "").strip()
    if not text:
        abort(400, "Paramètre 'text' requis")
    png = make_qr_png(text)
    return send_file(io.BytesIO(png), mimetype="image/png", download_name="qr.png")

@app.get("/api/config")
def api_config():
    return {"currency": EVENT_CURRENCY, "price_xof": EVENT_PRICE_XOF, "env": FEDAPAY_ENV}

@app.get("/api/ping")
def ping():
    return {"ok": True, "ts": datetime.utcnow().isoformat() + "Z"}

@app.get("/health")
def health():
    return {"status": "ok"}

# ───────────────────────── Entrée applicative ─────────────────────────
if __name__ == "__main__":
    print("==> Backend timeline prêt : http://127.0.0.1:5000 (Ctrl+C pour arrêter)")
    # ⚠️ En prod: debug=False, derrière un WSGI (gunicorn) + reverse-proxy (Nginx/Caddy)
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
