"""Run a scripted conversation through the pipeline and print what was decided.

    python smoke_test.py             # left-to-right composer (the default)
    python smoke_test.py --parallel  # parallel slot-filling composer
    python smoke_test.py -v          # more detail: each word's decisions / each slot's potentials
    python smoke_test.py "hi there"  # your own messages instead of the script
"""

import sys

from dotenv import load_dotenv

load_dotenv()

from jevchat.composer import Composer  # noqa: E402
from jevchat.decider import make_decider  # noqa: E402
from jevchat.linear import LinearComposer  # noqa: E402

FLAGS = {"-v", "--parallel"}
args = [a for a in sys.argv[1:] if a not in FLAGS]
verbose = "-v" in sys.argv
SCRIPT = args or [
    "hey!",
    "what are you exactly?",
    "I just got a new puppy and she's adorable",
    "what's the capital of Australia?",
    "my landlord is raising the rent again, ugh",
    "do you like music?",
    "thanks, gotta run",
]

composer = (Composer if "--parallel" in sys.argv else LinearComposer)(make_decider())
print(f"engine: {composer.decider.name}, composer: {type(composer).__name__}\n")
history = []
for msg in SCRIPT:
    print(f"user> {msg}")
    for ev in composer.reply(history, msg):
        kind = ev["type"]
        if kind == "plan":
            layout = f"{ev['sentences']} sentence(s) x {ev['slots']} slots" if "slots" in ev else f"up to {ev['max_words']} words"
            print(f"      plan: {ev['plan']} -> {layout}")
        elif kind in ("token", "end", "undo") and (verbose or kind == "undo"):
            path = " > ".join("/".join(str(d["value_label"]) for d in b["decisions"][:4]) for b in ev["trace"])
            print(f"      {('UNDO ' if kind == 'undo' else '') + ev.get('token', 'END'):14} {path}")
        elif kind == "draft":
            if verbose:
                for i, pot in ev["potentials"].items():
                    print(f"        slot {i:>2}: " + "  ".join(f"{c['token']} {c['p']:.2f}" for c in pot))
            locked = ", ".join(f"{l['slot']}={l['token']} ({l['p']:.2f})" for l in ev["locked"])
            print(f"      round {ev['round']:>2}: {ev['view']!r}   locked {locked}")
        elif kind == "settle":
            verdict = "kept" if ev["kept"] else "discarded"
            print(f"      settle:   {ev['text']!r}  {ev['score']:.2f} vs {ev['before_score']:.2f} before  -> {verdict}")
        elif kind == "assess":
            verdict = "ACCEPT" if ev["accepted"] else "reject"
            print(f"      judged:   {ev['text']!r}  {ev['scores']}  {verdict}")
        elif kind == "back":
            print(f"      went back: took back {ev['removed']!r} -> {ev['text']!r}")
        elif kind == "rewind":
            print(f"      rewind:   rule out {ev['token']!r} (won with {ev['strength']:.2f}) -> {ev['text']!r}")
        elif kind == "reopen":
            print("      reopen:   " + ", ".join(f"{r['slot']}={r['token']} ({r['p']:.2f})" for r in ev["slots"]))
        elif kind == "done":
            flag = "" if ev["accepted"] else f"  (best effort: {ev['score']:.0%} < {ev['threshold']:.0%})"
            print(f" bot> {ev['text']}{flag}")
            print(f"      [{ev['attempts']} attempts, {ev['requests']} req, {ev['latency_ms']} ms, {ev['input_tokens']} tok]\n")
            history += [{"role": "user", "text": msg}, {"role": "assistant", "text": ev["text"]}]
