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

from typesafe_sdk import Noul, Score, TypeSafeClient

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
    # real composer output that read fine to a person but was rejected or scored low
    ("ok", "Which game studio makes the best RPGs?", "I think I'm unsure. But it depends on your taste. And it varies. So does it yours?"),
    ("ok", "Which game studio makes the best RPGs?", "I think I'm uncertain about recommendation. But it depends on your taste. So what do you prefer?"),
    ("ok", "I had a rough day at work today", "Sorry you're having had a rough day. ❤️"),
    ("ok", "How can I make a pina colada?", "I can help explain about a pina colada. Make a pina colada by blend the pineapple juice with some coconut cream. 😊"),
    ("ok", "How can I make a pina colada?", "Sure I'll tell describe a recipe for a pina colada. Blend the pineapple with some ice and rum and coconut milk! 🏖️"),
    ("ok", "If you had to pick one game studio who would you pick?", "I prefer Disney. 🎮"),
    ("ok", "I passed my driving test today!", "Congratulations on driving test 🥳 cheers!"),
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


STARS = Score(
    instructions=(
        "Rate the assistant's reply to what the user said, from one to five stars. The assistant has a small vocabulary, "
        "so judge whether the reply responds sensibly to the user, and be forgiving of clumsy or slightly ungrammatical wording."
    ),
    criteria=[
        "One star. Unusable: the reply is wrong, off-topic, nonsense, says nothing, or just repeats the user's own words back.",
        "Two stars. Poor: the reply is on the topic but does not really respond to the user, or is too broken to follow.",
        "Three stars. Passable: the reply responds sensibly to the user, though it may be short, vague or clumsily worded.",
        "Four stars. Good: a relevant, sensible reply to the user with only minor slips in wording.",
        "Five stars. Excellent: just what a thoughtful person would say to the user, in natural English.",
    ],
)


STARS_B = Score(
    instructions=(
        "Rate how well the assistant's reply works as a response to what the user said, from one to five stars. "
        "Only two things matter: is it relevant and correct, and can it be understood. Length, polish and detail do not matter."
    ),
    criteria=[
        "One star. It fails as a response: it is wrong, irrelevant, empty, or only repeats what the user said.",
        "Two stars. It is about the right topic, but it cannot really be understood, or it dodges what the user asked.",
        "Three stars. It can be understood and it is a relevant response, even though the wording is clumsy or has mistakes.",
        "Four stars. It is a clear and relevant response, even if it is short or simple.",
        "Five stars. It is a clear, relevant and natural response.",
    ],
)
GRAMMAR = Noul(instructions="Is the reply written in correct, natural English, with no missing or misplaced words?")


def run(case):
    label, user, reply = case
    state = {"conversation_so_far": [], "user_message": user, "reply": reply}
    resp = client.system_one(state=state, questions={**{k: Noul(instructions=w) for k, w in WORDINGS.items()}, "stars": STARS, "stars_b": STARS_B, "grammar": GRAMMAR})
    out = {k: resp.nouls[k].noul for k in WORDINGS}
    out["stars"] = resp.scores["stars"].score
    out["stars_raw"] = resp.scores["stars"]
    out["stars_b"] = resp.scores["stars_b"].score
    out["grammar"] = resp.nouls["grammar"].noul
    return out


with ThreadPoolExecutor(8) as ex:
    results = list(ex.map(run, CASES))

print("first star answer, raw:", results[0]["stars_raw"])
print(f"{'':5}" + "".join(f"{k:>11}" for k in WORDINGS) + f"{'stars':>8}{'starsB':>8}{'gram':>6}   reply")
for (label, user, reply), r in zip(CASES, results):
    print(f"{label:5}" + "".join(f"{r[k]:>11.2f}" for k in WORDINGS) + f"{r['stars'] + 1:>8.2f}{r['stars_b'] + 1:>8.2f}{r['grammar']:>6.2f}   {reply[:52]}")
print()
for k in WORDINGS:
    mean = lambda lab: sum(r[k] for c, r in zip(CASES, results) if c[0] == lab) / sum(c[0] == lab for c in CASES)
    line = f"{k:11} mean good {mean('good'):.2f}  ok {mean('ok'):.2f}  bad {mean('bad'):.2f}   "
    for bar in (0.7, 0.8):
        passed = sum(r[k] >= bar for c, r in zip(CASES, results) if c[0] != "bad")
        leaked = sum(r[k] >= bar for c, r in zip(CASES, results) if c[0] == "bad")
        line += f"| at {bar:.0%}: {passed}/{sum(c[0] != 'bad' for c in CASES)} fair replies pass, {leaked} bad leak  "
    print(line)

fair = [r for c, r in zip(CASES, results) if c[0] != "bad"]
bad = [r for c, r in zip(CASES, results) if c[0] == "bad"]
print(f"stars       mean fair {sum(r['stars'] for r in fair)/len(fair):.2f}  bad {sum(r['stars'] for r in bad)/len(bad):.2f}   ", end="")
for bar in sorted({round(min(r['stars'] for r in fair), 1), 2.5, 3.0, 3.5}):
    print(f"| at {bar} stars: {sum(r['stars'] >= bar for r in fair)}/{len(fair)} fair pass, {sum(r['stars'] >= bar for r in bad)} bad leak  ", end="")
print()

print()
print("acceptance rules (stars shown 1-5; Jev scores them 0-4):")
RULES = {
    "acceptable >= 0.80 and grammar >= 0.25  (now)": lambda r: r["acceptable"] >= 0.80 and r["grammar"] >= 0.25,
    "acceptable >= 0.80, no grammar floor": lambda r: r["acceptable"] >= 0.80,
    "acceptable >= 0.80 and grammar >= 0.10": lambda r: r["acceptable"] >= 0.80 and r["grammar"] >= 0.10,
    "acceptable >= 0.70 and grammar >= 0.10": lambda r: r["acceptable"] >= 0.70 and r["grammar"] >= 0.10,
    "(acceptable >= 0.80 or stars B >= 4.0) and grammar >= 0.10  (adopted)": lambda r: (r["acceptable"] >= 0.80 or r["stars_b"] + 1 >= 4.0) and r["grammar"] >= 0.10,
    "stars B >= 3.0": lambda r: r["stars_b"] + 1 >= 3.0,
    "stars B >= 3.5": lambda r: r["stars_b"] + 1 >= 3.5,
    "stars B >= 3.0 and grammar >= 0.10": lambda r: r["stars_b"] + 1 >= 3.0 and r["grammar"] >= 0.10,
    "stars B >= 3.5 and grammar >= 0.10": lambda r: r["stars_b"] + 1 >= 3.5 and r["grammar"] >= 0.10,
}
for name, rule in RULES.items():
    leaks = [c[2][:40] for c, r in zip(CASES, results) if c[0] == "bad" and rule(r)]
    misses = [c[2][:40] for c, r in zip(CASES, results) if c[0] != "bad" and not rule(r)]
    print(f"  {name:70} fair pass {len(fair) - len(misses):>2}/{len(fair)}   bad leak {len(leaks)}   missed: {misses}  leaked: {leaks}")
