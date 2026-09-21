"""Is the judge too harsh, and can its question be loosened without letting junk through?

Each case is labelled by hand: good (should pass), ok (imperfect but a fair reply from a
limited speaker; should pass at a modest bar), bad (should fail). A wording is better when it
lifts good and ok replies while keeping bad ones low.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
from concurrent.futures import ThreadPoolExecutor

from typesafe_sdk import Noul, TypeSafeClient

client = TypeSafeClient()

CASES = [
    # small talk
    ("good", "I had a rough day at work today", "Sorry about your day. What happened?"),
    ("good", "I just got a new puppy and she's adorable", "Wow she's lovely! How is she?"),
    ("good", "I love hiking in the mountains", "Where do you like to hike?"),
    ("good", "hey!", "Hello!"),
    # direct questions, short but right
    ("good", "do you like music?", "I do! I love music."),
    ("good", "what's the capital of Australia?", "It's Canberra."),
    ("good", "what is 2 plus 2?", "Four."),
    # opinion and how-to, fair answers from a limited speaker
    ("ok", "Which game studio makes the best RPGs?", "I think it depends on taste in games."),
    ("ok", "If you had to pick one studio though who would you pick?", "I'd pick Valve because they are awesome."),
    ("ok", "How can I make a pina colada?", "Mix rum, coconut cream and pineapple juice with ice."),
    ("ok", "How can I make a pina colada?", "Blend pineapple, coconut and rum. Serve it cold."),
    ("ok", "why is the sky blue?", "Because the air scatters blue light from the sun the most."),
    ("ok", "do you like music?", "Yes I do."),
    ("ok", "my landlord is raising the rent again", "Sorry about the rent situation."),
    # rough grammar but the point lands
    ("ok", "If you had to pick one studio though who would you pick?", "I'd pick valve because awesome definitely."),
    ("ok", "I just got a new puppy and she's adorable", "Wow she's cute! What name is she?"),
    # bad: wrong, empty, evasive, parroted, off-topic or broken
    ("bad", "what's the capital of Australia?", "The city is Sydney."),
    ("bad", "How can I make a pina colada?", "Mix the ingredients of a pina colada with ice."),
    ("bad", "How can I make a pina colada?", "I love music."),
    ("bad", "I love hiking in the mountains", "I love hiking in the mountains."),
    ("bad", "I had a rough day at work today", "Wow awesome!"),
    ("bad", "I had a rough day at work today", "Sorry, are you feel rough?. What do did you thing do happened?"),
    ("bad", "Which game studio makes the best RPGs?", "Games."),
    ("bad", "why is the sky blue?", "It's because light depends of spectrum of the radiation of the effect."),
    ("bad", "my grandfather passed away last week", "Congratulations!"),
    ("bad", "what is 2 plus 2?", "Seven."),
]

WORDINGS = {
    "current": (
        "Is this a good reply to what the user said? A good reply speaks to what the user actually said, "
        "in a way a thoughtful person would find fitting and sensible."
    ),
    "acceptable": (
        "Is this an acceptable reply to what the user said? It is acceptable if it speaks to what the user actually "
        "said and makes sense. It does not have to be detailed, complete or perfectly worded: a short, relevant, "
        "sensible reply counts. It is not acceptable if it is wrong, off-topic, empty of content, or just repeats the user."
    ),
    "friend": (
        "Imagine a friendly person with a small vocabulary said this in reply to the user. Would the user feel "
        "that they were understood and given a relevant, sensible response? Answer no if the reply is wrong, "
        "off-topic, says nothing, or just repeats the user."
    ),
}


def run(case):
    label, user, reply = case
    state = {"conversation_so_far": [], "user_message": user, "reply": reply}
    a = client.system_one(state=state, questions={k: Noul(instructions=w) for k, w in WORDINGS.items()}).nouls
    return {k: a[k].noul for k in WORDINGS}


with ThreadPoolExecutor(8) as ex:
    results = list(ex.map(run, CASES))

print(f"{'':5}" + "".join(f"{k:>11}" for k in WORDINGS) + "   reply")
for (label, user, reply), r in zip(CASES, results):
    print(f"{label:5}" + "".join(f"{r[k]:>11.2f}" for k in WORDINGS) + f"   {reply[:60]}")
print()
for k in WORDINGS:
    mean = lambda lab: sum(r[k] for c, r in zip(CASES, results) if c[0] == lab) / sum(c[0] == lab for c in CASES)
    line = f"{k:11} mean good {mean('good'):.2f}  ok {mean('ok'):.2f}  bad {mean('bad'):.2f}   "
    for bar in (0.7, 0.8):
        passed = sum(r[k] >= bar for c, r in zip(CASES, results) if c[0] != "bad")
        leaked = sum(r[k] >= bar for c, r in zip(CASES, results) if c[0] == "bad")
        line += f"| at {bar:.0%}: {passed}/{sum(c[0] != 'bad' for c in CASES)} fair replies pass, {leaked} bad leak  "
    print(line)
