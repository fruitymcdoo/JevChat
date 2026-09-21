"""Does discouraging sentence endings help? Same messages, first draft only, at several CLOSE_WEIGHTs.

    python experiments/close_weight.py 1.0 0.9 0.8
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
from concurrent.futures import ThreadPoolExecutor

import jevchat.linear as linear
from jevchat.decider import make_decider

MESSAGES = [
    "Which game studio makes the best RPGs?",
    "I had a rough day at work today",
    "How can I make a pina colada?",
    "I love hiking in the mountains",
    "why is the sky blue?",
    "my landlord is raising the rent again, ugh",
]
weights = [float(a) for a in sys.argv[1:]] or [1.0, 0.8]
linear.MAX_ATTEMPTS = 1  # first drafts only: the cleanest view of what the weight does


def run(message):
    events = list(linear.LinearComposer(make_decider()).reply([], message))
    judged = next(e for e in events if e["type"] == "assess")
    return {"text": judged["text"], **judged["scores"], "backs": sum(e["type"] == "back" for e in events),
            "words": len(judged["text"].split()), "tokens": events[-1]["input_tokens"]}


for w in weights:
    linear.CLOSE_WEIGHT = w  # read at call time by the comparison step
    with ThreadPoolExecutor(3) as ex:
        results = list(ex.map(run, MESSAGES))
    print(f"\nCLOSE_WEIGHT = {w}")
    for m, r in zip(MESSAGES, results):
        print(f"  {r['responds']:.2f} resp  {r['grammatical']:.2f} gram  {r['words']:>2} words  {r['backs']} back  {r['text']!r}")
    n = len(results)
    print(f"  mean: responds {sum(r['responds'] for r in results)/n:.2f}, grammatical {sum(r['grammatical'] for r in results)/n:.2f}, "
          f"words {sum(r['words'] for r in results)/n:.1f}, go-backs {sum(r['backs'] for r in results)}, "
          f"tokens {sum(r['tokens'] for r in results)/n/1000:.0f}k per reply")
