import io
import base64
from datetime import datetime
from flask import Flask, request, send_file, jsonify, render_template_string, abort
from flask_cors import CORS
import qrcode
import qrcode.image.svg as qrcode_svg

app = Flask(__name__)
# Autoriser les appels cross-origin vers l'API (utile depuis un site statique distant)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ---------- Utilitaires ----------
MAX_LEN = 280  # limite simple pour éviter les abus

def build_payload(nom: str, prenom: str) -> str:
    """Construit le texte encodé dans le QR (format JSON lisible)."""
    nom = (nom or "").strip()
    prenom = (prenom or "").strip()
    if not nom or not prenom:
        abort(400, "Champs 'nom' et 'prenom' requis")
    if len(nom) > MAX_LEN or len(prenom) > MAX_LEN:
        abort(413, "Champs trop longs (max 280 caractères)")
    # Tu peux changer le format si tu veux (vCard, texte libre, etc.)
    return f'{{"nom":"{nom}","prenom":"{prenom}","ts":"{datetime.utcnow().isoformat()}Z"}}'

def make_qr_png(data: str) -> bytes:
    """Génère un PNG en mémoire."""
    qr = qrcode.QRCode(
        version=None,  # auto
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

# ---------- Pages ----------
INDEX = """
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Fournisseur de QR</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html,body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
    main{max-width:720px;margin:3rem auto;padding:0 1rem;line-height:1.6}
    form{display:flex;flex-wrap:wrap;gap:.5rem;margin:.5rem 0}
    input,button,select{padding:.6rem .8rem}
    img{max-width:280px;border:1px solid #eee;border-radius:.5rem;padding:.5rem;background:#fff}
    .row{display:flex;gap:.5rem;align-items:center}
    code{background:#f6f6f6;padding:.2rem .4rem;border-radius:.25rem}
  </style>
</head>
<body>
<main>
  <h1>Fournisseur de QR</h1>
  <p>Test rapide : génère un QR à partir d’un <strong>nom</strong> et <strong>prénom</strong>.</p>
  <form id="f">
    <input name="nom" placeholder="Nom" required>
    <input name="prenom" placeholder="Prénom" required>
    <select name="format">
      <option value="png" selected>PNG</option>
      <option value="svg">SVG</option>
    </select>
    <button>Générer</button>
  </form>
  <div id="out"></div>
  <hr>
  <p><strong>API</strong> :</p>
  <ul>
    <li>POST <code>/api/qr</code> → image (PNG par défaut, ou <code>?format=svg</code>)</li>
    <li>POST <code>/api/qr?response=json</code> → <code>{"data_url": "data:image/png;base64,..."}</code></li>
  </ul>
  <p>Exemple site statique :</p>
  <pre><code>fetch("https://&lt;ton-service&gt;.onrender.com/api/qr?response=json", {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({ nom: "Alice", prenom: "Dupont" })
}).then(r =&gt; r.json()).then(({data_url}) =&gt; {
  document.querySelector("img").src = data_url;
});</code></pre>
  <script>
    const f = document.getElementById('f');
    const out = document.getElementById('out');
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(f).entries());
      const fmt = data.format || 'png';
      const resp = await fetch(`/api/qr?response=json&format=${fmt}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nom: data.nom, prenom: data.prenom })
      });
      const json = await resp.json();
      out.innerHTML = `<img alt="QR" src="${json.data_url}">`;
    });
  </script>
</main>
</body>
</html>
"""

@app.get("/")
def index():
    return render_template_string(INDEX)

@app.get("/qr")
def preview_qr():
    """GET /qr?text=... → renvoie un PNG rapide (debug/preview)."""
    text = (request.args.get("text") or "").strip()
    if not text:
        abort(400, "Paramètre 'text' requis")
    png = make_qr_png(text)
    return send_file(io.BytesIO(png), mimetype="image/png", download_name="qr.png")

# ---------- API ----------
@app.post("/api/qr")
def api_qr():
    """
    POST JSON: {"nom":"...", "prenom":"..."}
    Query:
      - format=png|svg (par défaut: png)
      - response=json → renvoie {"data_url": "..."} au lieu de l'image brute
    """
    payload = request.get_json(silent=True) or {}
    nom = payload.get("nom")
    prenom = payload.get("prenom")
    data = build_payload(nom, prenom)

    fmt = (request.args.get("format") or "png").lower()
    as_json = (request.args.get("response") == "json")

    if fmt == "svg":
        svg = make_qr_svg(data)
        if as_json:
            b64 = base64.b64encode(svg).decode()
            return jsonify({"data_url": f"data:image/svg+xml;base64,{b64}"})
        return send_file(io.BytesIO(svg), mimetype="image/svg+xml", download_name="qr.svg")

    # défaut: PNG
    png = make_qr_png(data)
    if as_json:
        b64 = base64.b64encode(png).decode()
        return jsonify({"data_url": f"data:image/png;base64,{b64}"})
    return send_file(io.BytesIO(png), mimetype="image/png", download_name="qr.png")

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    print("==> QR Provider sur http://127.0.0.1:5000 (Ctrl+C pour arrêter)")
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
