"""Run a scripted conversation through the pipeline and print what was decided.

    python smoke_test.py            # each round's draft, then scores
    python smoke_test.py -v         # also each slot's potentials
    python smoke_test.py "hi there" # your own messages instead of the script
"""

import sys

from dotenv import load_dotenv

load_dotenv()

from jevchat.composer import Composer  # noqa: E402
from jevchat.decider import make_decider  # noqa: E402

args = [a for a in sys.argv[1:] if a != "-v"]
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

composer = Composer(make_decider())
print(f"engine: {composer.decider.name}\n")
history = []
for msg in SCRIPT:
    print(f"user> {msg}")
    for ev in composer.reply(history, msg):
        if ev["type"] == "plan":
            print(f"      plan: {ev['plan']} -> {ev['sentences']} sentence(s) x {ev['slots']} slots")
        elif ev["type"] == "draft":
            if verbose:
                for i, pot in ev["potentials"].items():
                    print(f"        slot {i:>2}: " + "  ".join(f"{c['token']} {c['p']:.2f}" for c in pot))
            locked = ", ".join(f"{l['slot']}={l['token']} ({l['p']:.2f})" for l in ev["locked"])
            print(f"      round {ev['round']:>2}: {ev['view']!r}   locked {locked}")
        elif ev["type"] == "settle":
            verdict = "kept" if ev["kept"] else "discarded"
            print(f"      settle:   {ev['text']!r}  {ev['score']:.2f} vs {ev['before_score']:.2f} before  -> {verdict}")
        elif ev["type"] == "assess":
            verdict = "ACCEPT" if ev["accepted"] else "reject"
            print(f"      judged:   {ev['text']!r}  {ev['scores']}  {verdict}")
        elif ev["type"] == "reopen":
            print("      reopen:   " + ", ".join(f"{r['slot']}={r['token']} ({r['p']:.2f})" for r in ev["slots"]))
        elif ev["type"] == "done":
            flag = "" if ev["accepted"] else f"  (best effort: {ev['score']:.0%} < {ev['threshold']:.0%})"
            print(f" bot> {ev['text']}{flag}")
            print(f"      [{ev['attempts']} attempts, {ev['rounds']} rounds, {ev['requests']} req, {ev['latency_ms']} ms, {ev['input_tokens']} tok]\n")
            history += [{"role": "user", "text": msg}, {"role": "assistant", "text": ev["text"]}]
