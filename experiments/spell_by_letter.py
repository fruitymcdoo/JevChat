"""Can Jev find the right word by spelling it, one letter at a time?

Same known-answer cases as options_8_vs_flat.py: a sensible reply with one word blanked.
Each step is a 27-way choice (a-z, or "the word is finished"), greedy, with no dictionary.
Also reports how much probability Jev put on the true letter at each step.
"""
import math
import pathlib
import string
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
from concurrent.futures import ThreadPoolExecutor

from typesafe_sdk import Choice, TypeSafeClient

from jevchat.composer import WRITING

client = TypeSafeClient()
src = open(ROOT / "experiments" / "options_8_vs_flat.py", encoding="utf-8").read()
CASES = eval(src[src.index("CASES = [") + 8: src.index("]\n\ndef semantic_key") + 1])
CASES += [  # words no frequency dictionary of ours contains
    ("what's the capital of Australia?", "the capital of Australia is [?] .", "canberra"),
    ("who wrote Hamlet?", "it was written by william [?] .", "shakespeare"),
    ("what's the chemical symbol Na?", "na stands for [?] .", "sodium"),
]
MAX_LETTERS = 16
LETTERS = {c: f'The letter "{c}".' for c in string.ascii_lowercase}


def ask(state, instructions, criteria):
    a = client.system_one(state=state, questions={"q": Choice(instructions=instructions, criteria=criteria)}).choices["q"]
    return a.choice, dict(a.probabilities)


def run(case):
    user, reply, answer = case
    state = {"conversation_so_far": [], "user_message": user}
    head = WRITING + f'Here is the reply so far, with the slot in question marked [?]: "{reply}". '

    def step(spelled):
        shown = f'"{spelled}"' if spelled else "nothing yet"
        criteria = dict(LETTERS)
        if spelled:
            criteria["DONE"] = f'No more letters. The word is complete: "{spelled}".'
        return ask(state, head + f"We are spelling the word for the marked slot one letter at a time. "
                                 f"So far we have spelled {shown}. What is the next letter?", criteria)

    # free run: greedy spelling with nothing to guide it
    spelled = ""
    while len(spelled) < MAX_LETTERS:
        pick, _ = step(spelled)
        if pick == "DONE":
            break
        spelled += pick

    # teacher-forced: how much probability lands on the true letter at each step?
    p_true = []
    for i in range(len(answer) + 1):
        _, probs = step(answer[:i])
        p_true.append(probs.get(answer[i] if i < len(answer) else "DONE", 0.0))
    return {"answer": answer, "spelled": spelled, "p_true": p_true, "requests": len(spelled) + 1}


with ThreadPoolExecutor(8) as ex:
    results = list(ex.map(run, CASES))

for r in results:
    mark = "Y" if r["spelled"] == r["answer"] else "."
    print(f"{mark} {r['answer']:12} -> {r['spelled']:16} P(true letter): " + " ".join(f"{p:.2f}" for p in r["p_true"]))
n = len(results)
print(f"\nexact word recovered: {sum(r['spelled'] == r['answer'] for r in results)}/{n}")
print(f"mean requests per word: {sum(r['requests'] for r in results) / n:.1f}")
first = sum(r["p_true"][0] for r in results) / n
rest = [p for r in results for p in r["p_true"][1:]]
print(f"mean P(true letter): first letter {first:.2f}, later letters {sum(rest) / len(rest):.2f}")
