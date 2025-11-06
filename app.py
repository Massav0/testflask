from flask import Flask, request, redirect, url_for, jsonify, render_template_string
from datetime import datetime

app = Flask(__name__)

# "Base de données" en mémoire (simple pour apprendre)
NOTES = []
NEXT_ID = 1

PAGE = """
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Mini Notes (Flask)</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html, body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
    main { max-width: 720px; margin: 3rem auto; padding: 0 1rem; line-height: 1.6; }
    h1 { margin-bottom: 0.5rem; }
    form { display: flex; gap: .5rem; margin: 1rem 0; }
    input[type="text"] { flex: 1; padding: .6rem .8rem; }
    button { padding: .6rem 1rem; cursor: pointer; }
    ul { list-style: none; padding: 0; }
    li { display: flex; align-items: center; justify-content: space-between;
         border: 1px solid #ddd; padding: .6rem .8rem; border-radius: .5rem; margin: .4rem 0; }
    small { color: #666; }
    .muted { color: #777; font-style: italic; }
    .row { display:flex; gap:.5rem; align-items:center; }
    .right { display:flex; gap:.5rem; align-items:center; }
  </style>
</head>
<body>
<main>
  <h1>Mini Notes (Flask)</h1>
  <p class="muted">Démo : ajoute des notes, liste-les, supprime-les. API dispo sur <code>/api/notes</code>.</p>

  <form method="post" action="{{ url_for('add_note') }}">
    <input type="text" name="text" placeholder="Écrire une note..." required>
    <button type="submit">Ajouter</button>
  </form>

  {% if notes %}
    <ul>
      {% for n in notes %}
        <li>
          <div class="row">
            <strong>#{{ n.id }}</strong>
            <span>{{ n.text }}</span>
            <small>— {{ n.created_at }}</small>
          </div>
          <div class="right">
            <form method="post" action="{{ url_for('delete_note', note_id=n.id) }}">
              <button type="submit" title="Supprimer">🗑️</button>
            </form>
          </div>
        </li>
      {% endfor %}
    </ul>
  {% else %}
    <p class="muted">Aucune note pour l’instant.</p>
  {% endif %}

  <hr>
  <p><strong>API</strong> : <a href="{{ url_for('list_notes_api') }}">{{ request.host_url.rstrip('/') + url_for('list_notes_api') }}</a></p>
  <p><strong>Health</strong> : <a href="{{ url_for('health') }}">{{ request.host_url.rstrip('/') + url_for('health') }}</a></p>
</main>
</body>
</html>
"""

@app.get("/")
def index():
    return render_template_string(PAGE, notes=NOTES)

@app.post("/add")
def add_note():
    global NEXT_ID
    text = request.form.get("text", "").strip()
    if text:
        NOTES.append({
            "id": NEXT_ID,
            "text": text,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        NEXT_ID += 1
    return redirect(url_for("index"))

@app.post("/delete/<int:note_id>")
def delete_note(note_id: int):
    global NOTES
    NOTES = [n for n in NOTES if n["id"] != note_id]
    return redirect(url_for("index"))

# --- API JSON ---
@app.get("/api/notes")
def list_notes_api():
    return jsonify(NOTES)

@app.post("/api/notes")
def create_note_api():
    global NEXT_ID
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    if not text:
        return jsonify({"error": "text requis"}), 400
    note = {"id": NEXT_ID, "text": text, "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    NOTES.append(note)
    NEXT_ID += 1
    return jsonify(note), 201

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    print("==> Lancement Mini Notes sur http://127.0.0.1:5000  (Ctrl+C pour arrêter)")
    # use_reloader=False évite certains soucis Windows
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
