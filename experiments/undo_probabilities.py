"""How sure is Jev when it chooses to go back? Prints the go-back probability at every comparison step.

    python experiments/undo_probabilities.py "Which game studio makes the best RPGs?"
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from jevchat.decider import make_decider
from jevchat.linear import LinearComposer

message = sys.argv[1] if len(sys.argv) > 1 else "Which game studio makes the best RPGs?"
composer = LinearComposer(make_decider())
for ev in composer.reply([], message):
    if ev["type"] in ("token", "back", "end"):
        for b in ev["trace"]:
            if b["stage"] != "compare texts":
                continue
            d = b["decisions"][0]
            undo = next((p["p"] for p in d["probabilities"] if p["option"] == "UNDO"), None)
            top = ", ".join(f"{p['label']} {p['p']:.2f}" for p in d["probabilities"][:3])
            what = {"token": ev.get("token"), "back": f"BACK ({ev.get('removed')})", "end": "END"}[ev["type"]]
            shown = "  n/a" if undo is None else f"{undo:5.2f}"
            print(f"go-back {shown} | {what!s:24} | top options: {top}")
    elif ev["type"] == "assess":
        print(f"judged: {ev['text']!r} {ev['scores']}")
        break
