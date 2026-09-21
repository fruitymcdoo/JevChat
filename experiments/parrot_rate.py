"""Why does it repeat the user's message back, and does more task context fix it?

Compares prompt preambles on three probes:
  compare   the whole-text comparison step: given four candidate replies, how much
            probability goes to the ones that just repeat the user?
  first     the first slot of an empty reply: how likely is Jev to reach for the
            user's own words (the "echo" kind)?
  words     the word-picking step for a slot with blank neighbours: from a list of 255
            nouns (or verbs) that includes the user's own, how much probability lands
            on the user's words?
  e2e       full replies from the real composer: what share of the reply's words
            were the user's, and how does the judge score it?   (pass --e2e; slow, ~$0.30)
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
from concurrent.futures import ThreadPoolExecutor

from typesafe_sdk import Choice, TypeSafeClient

import jevchat.composer as composer
from jevchat.decider import make_decider

client = TypeSafeClient()

# (user message, parrot, half-parrot with pronouns swapped, two good replies)
CASES = [
    ("I love hiking in the mountains", "I love hiking in the mountains.", "You love hiking in the mountains.", "Where do you like to hike?", "That sounds wonderful!"),
    ("I had a rough day at work today", "I had a rough day at work today.", "You had a rough day at work today.", "Sorry to hear that. What happened?", "That sounds hard."),
    ("I just got a new puppy", "I just got a new puppy.", "You just got a new puppy.", "Congratulations! What is her name?", "How exciting!"),
    ("my landlord is raising the rent again", "My landlord is raising the rent again.", "Your landlord is raising the rent again.", "That is not fair. Can you move?", "Sorry, that sounds stressful."),
    ("I'm learning to cook", "I'm learning to cook.", "You are learning to cook.", "What have you made so far?", "That is a great skill to have."),
    ("it's been raining all week here", "It's been raining all week here.", "It has been raining all week there.", "I hope the sun comes out soon.", "That sounds miserable."),
    ("my sister is getting married next month", "My sister is getting married next month.", "Your sister is getting married next month.", "Congratulations to her! Are you excited?", "That is wonderful news!"),
    ("I can't sleep lately", "I can't sleep lately.", "You can't sleep lately.", "That sounds exhausting. How long has it been?", "Sorry to hear that."),
    ("I failed my driving test", "I failed my driving test.", "You failed your driving test.", "Do not worry, you can try again.", "Sorry, that is frustrating."),
    ("I just finished reading a great book", "I just finished reading a great book.", "You just finished reading a great book.", "What was the book about?", "Nice! Who wrote it?"),
]
E2E = [c[0] for c in CASES[:5]]

CURRENT = (  # the preamble before this experiment
    "We are writing the assistant's reply to the user's message, following the plan for the reply. "
    "The reply is a row of slots, and each slot holds one word or one punctuation mark. "
)
TASK = (
    "This is a conversation between two people, a user and an assistant. The user has just spoken, and now it is "
    "the assistant's turn. We are writing what the assistant says back, following the plan for the reply. "
    "The assistant speaks for itself: in its reply, \"I\" means the assistant and \"you\" means the user. "
    "A good reply reacts to what the user said: it answers, sympathises, congratulates, or asks something new. "
    "It never simply says the user's own sentence back to them, because the user already knows what they said. "
    "The reply is a row of slots, and each slot holds one word or one punctuation mark. "
)
HINT = (" The reply should bring something new, so prefer a word the user did not already use, "
        "unless the reply has to name the very same thing.")
VARIANTS = {"current": CURRENT, "task context": TASK, "context + hint": TASK}
WORD_HINT = {"context + hint": HINT}


def plan_for(user):
    state = {"conversation_so_far": [], "user_message": user}
    qs = {qid: Choice(instructions=i, criteria=c) for qid, (i, c) in composer.PLAN.items()}
    picks = client.system_one(state=state, questions=qs).choices
    return {qid: composer.PLAN[qid][1][a.choice] for qid, a in picks.items()}


NODES = {n["key"]: n for n in composer.load_lexicon()["tree"]}


def words(text):
    return re.findall(r"[a-z']+", text.lower())


def probe(case):
    user, parrot, half, good1, good2 = case
    state = {"conversation_so_far": [], "user_message": user, "reply_plan": plan_for(user)}
    out = {}
    for name, preamble in VARIANTS.items():
        texts = {"c0": parrot, "c1": good1, "c2": half, "c3": good2}
        compare = Choice(
            instructions=preamble + "Each option shows the whole reply with a different choice for slot 1. "
            "Which option is the most grammatical, natural English and the best reply to the user?",
            criteria={k: f'"{t}"' for k, t in texts.items()},
        )
        blank = ["[?]"] + ["___"] * 5
        kinds = {n["key"]: n["desc"] for n in composer.load_lexicon()["tree"]}
        kinds["echo"] = "A word repeated from the user's own message, such as a name or the specific thing they mentioned."
        first = Choice(
            instructions=preamble
            + f'Here is the reply so far, with the slot in question marked [?] and slots not yet filled shown as ___: "{" ".join(blank)}". '
            "The marked slot is slot 1 of the 6 slots in the sentence being written. "
            "What has to go in the marked slot so that the whole reply becomes natural, grammatical English that responds to the user?",
            criteria=kinds,
        )
        questions = {"compare": compare, "first": first}
        said = set(words(user))
        mine = {}
        for kind in ("noun", "verb"):
            lists = NODES[kind]["lists"]
            theirs = [w for l in lists for w in l if w in said]
            if not theirs:
                continue
            options = list(dict.fromkeys(theirs + lists[0]))[:255]
            mine[kind] = theirs
            shown = "___ ___ [?] ___ ___ ."
            questions[kind] = Choice(
                instructions=preamble
                + f'Here is the reply so far, with the slot in question marked [?] and slots not yet filled shown as ___: "{shown}". '
                "The marked slot is slot 3 of the 6 slots in the sentence being written. "
                f"Suppose the marked slot holds {composer.a_kind(kind)}. Exactly which word is it?" + WORD_HINT.get(name, ""),
                criteria={w: None for w in options},
            )
        a = client.system_one(state=state, questions=questions).choices
        copied = [sum(a[k].probabilities.get(w, 0.0) for w in mine[k]) for k in mine]
        p = a["compare"].probabilities
        out[name] = {"parrot": p["c0"] + p["c2"], "picked_parrot": a["compare"].choice in ("c0", "c2"),
                     "echo": a["first"].probabilities.get("echo", 0.0),
                     "copied": sum(copied) / len(copied) if copied else 0.0}
    return out


def e2e(user):
    out = {}
    for name, preamble in VARIANTS.items():
        if name == "task context":
            continue  # e2e compares before and after only
        composer.WRITING = preamble  # every composing prompt reads these at call time
        composer.WORD_HINT = WORD_HINT.get(name, "")
        done = [ev for ev in composer.Composer(make_decider()).reply([], user) if ev["type"] == "done"][0]
        reply, said = words(done["text"]), set(words(user))
        out[name] = {"text": done["text"], "score": done["score"],
                     "overlap": sum(w in said for w in reply) / max(1, len(reply))}
    return out


with ThreadPoolExecutor(5) as ex:
    probes = list(ex.map(probe, CASES))
print(f"{'':42}" + "".join(f"{v:>24}" for v in VARIANTS))
print(f"{'user message':42}" + "  parrot  echo  copied" * len(VARIANTS))
for case, r in zip(CASES, probes):
    print(f"{case[0][:40]:42}" + "".join(f"{r[v]['parrot']:>8.2f}{r[v]['echo']:>6.2f}{r[v]['copied']:>8.2f}    " for v in VARIANTS))
n = len(probes)
for v in VARIANTS:
    print(f"\n{v}: compare step picks a parrot {sum(r[v]['picked_parrot'] for r in probes)}/{n} times, "
          f"mean P(parrot) {sum(r[v]['parrot'] for r in probes) / n:.2f}; "
          f"mean P(echo kind) at first slot {sum(r[v]['echo'] for r in probes) / n:.2f}; "
          f"mean probability on the user's own words when picking a word {sum(r[v]['copied'] for r in probes) / n:.2f}")

if "--e2e" in sys.argv:
    print("\nfull replies (one variant at a time; the composer is not thread-safe across preambles)")
    for user in E2E:
        r = e2e(user)
        print(f"\n  user: {user}")
        for v in r:
            print(f"    {v:15} {r[v]['score']:.2f}  overlap {r[v]['overlap']:.0%}  {r[v]['text']!r}")
