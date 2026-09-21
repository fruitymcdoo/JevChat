import json
import secrets
import uuid

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, session, stream_with_context
from typesafe_sdk import TypeSafeError

load_dotenv()

from jevchat.composer import ACCEPT_THRESHOLD, Composer  # noqa: E402  (needs env loaded first)
from jevchat.decider import make_decider  # noqa: E402
from jevchat.linear import LinearComposer  # noqa: E402

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)  # sessions reset on restart; fine for a prototype

decider = make_decider()
composers = {"linear": LinearComposer(decider), "parallel": Composer(decider)}  # first is the default
conversations: dict[str, list[dict]] = {}  # in-memory; swap for a store later


def _history() -> list[dict]:
    sid = session.setdefault("sid", uuid.uuid4().hex)
    return conversations.setdefault(sid, [])


@app.get("/")
def index():
    return render_template("index.html", engine=decider.name, threshold=round(ACCEPT_THRESHOLD * 100))


@app.post("/api/chat")
def chat():
    """Streams the reply as newline-delimited JSON events, one per decided word."""
    body = request.get_json(silent=True) or {}
    message = str(body.get("message", "")).strip()
    thesaurus = bool(body.get("thesaurus", True))
    composer = composers.get(body.get("mode"), composers["linear"])
    try:
        threshold = min(max(float(body.get("threshold", ACCEPT_THRESHOLD)), 0.0), 1.0)
    except (TypeError, ValueError):
        return jsonify(error="threshold must be a number between 0 and 1."), 400
    if not message:
        return jsonify(error="Empty message."), 400
    if len(message) > 2000:
        return jsonify(error="Message too long (2000 characters max)."), 400

    history = _history()

    def events():
        try:
            for ev in composer.reply(history, message, thesaurus=thesaurus, threshold=threshold):
                if ev["type"] == "done":
                    history.append({"role": "user", "text": message})
                    history.append({"role": "assistant", "text": ev["text"]})
                yield json.dumps(ev) + "\n"
        except TypeSafeError as e:
            app.logger.exception("Jev request failed")
            yield json.dumps({"type": "error", "error": f"Jev request failed: {type(e).__name__}"}) + "\n"

    return Response(stream_with_context(events()), mimetype="application/x-ndjson")


@app.post("/api/reset")
def reset():
    _history().clear()
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
