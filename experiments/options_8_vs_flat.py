"""Does Jev pick words better through rounds of 8 than from one big list?

Known-answer test: a sensible reply with one word blanked. The true word sits in a
dictionary leaf of up to 255 words. Recover it (a) flat, (b) via 8-way rounds where
each option is a group of words, grouped semantically (WordNet) or (c) by frequency.
"""
import sys, math
import pathlib; ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from concurrent.futures import ThreadPoolExecutor
from nltk.corpus import wordnet as wn
from typesafe_sdk import Choice, TypeSafeClient
from jevchat.composer import load_lexicon, WRITING

client = TypeSafeClient()
lex = load_lexicon()
leaves = []
for n in lex["tree"]:
    for leaf in n.get("children", [n]):
        leaves.append((n["key"], leaf["words"]))

# (user message, reply with [?], answer)
CASES = [
    ("I had a rough day at work today", "sorry about your [?] .", "day"),
    ("I had a rough day at work today", "that sounds really [?] .", "hard"),
    ("I just got a new puppy!", "what is her [?] ?", "name"),
    ("I just got a new puppy!", "that is [?] news !", "great"),
    ("do you like music?", "yes , i [?] music .", "love"),
    ("do you like music?", "what kind of [?] do you like ?", "music"),
    ("I love hiking in the mountains", "where do you [?] hiking ?", "go"),
    ("I love hiking in the mountains", "the [?] must be beautiful up there .", "view"),
    ("my landlord is raising the rent again", "that is not [?] .", "fair"),
    ("my landlord is raising the rent again", "can you [?] somewhere cheaper ?", "move"),
    ("I can't sleep lately", "how long has this been [?] ?", "happening"),
    ("I can't sleep lately", "that sounds [?] .", "exhausting"),
    ("I'm learning to cook", "what did you [?] first ?", "make"),
    ("I'm learning to cook", "what is your favorite [?] so far ?", "dish"),
    ("my sister is getting married next month", "are you [?] about the wedding ?", "excited"),
    ("my sister is getting married next month", "that is wonderful [?] !", "news"),
    ("I failed my driving test", "you can [?] again soon .", "try"),
    ("I failed my driving test", "do not [?] , many people fail the first time .", "worry"),
    ("it's been raining all week here", "i hope the [?] comes out soon .", "sun"),
    ("it's been raining all week here", "that sounds [?] .", "miserable"),
    ("I just finished reading a great book", "what was the [?] about ?", "book"),
    ("I just finished reading a great book", "who [?] it ?", "wrote"),
    ("thanks for the help", "you are [?] !", "welcome"),
    ("I'm thinking about getting a cat", "cats make wonderful [?] .", "pets"),
]

def semantic_key(w):
    for pos in "nvar":
        lemma = wn.morphy(w, pos)
        if lemma and wn.synsets(lemma, pos):
            s = wn.synsets(lemma, pos)[0]
            path = s.hypernym_paths()[0] if s.hypernym_paths() else [s]
            return " ".join(x.name() for x in path)
    return "~" + w

SEM = {w: semantic_key(w) for _, ws in leaves for w in ws}

def ask(state, instructions, criteria):
    a = client.system_one(state=state, questions={"q": Choice(instructions=instructions, criteria=criteria)}).choices["q"]
    return a.choice, dict(a.probabilities)

def chunks(words, k=8):
    size = math.ceil(len(words) / k)
    return [words[i:i + size] for i in range(0, len(words), size)]

def run(case):
    user, reply, answer = case
    found = [(k, ws) for k, ws in leaves if answer in ws]
    if not found:
        return None
    kind, words = max(found, key=lambda kw: len(kw[1]))
    state = {"conversation_so_far": [], "user_message": user}
    head = WRITING + f'Here is the reply so far, with the slot in question marked [?]: "{reply}". '
    out = {"answer": answer, "n": len(words)}

    pick, probs = ask(state, head + "Exactly which word belongs in the marked slot?", {w: None for w in words})
    out["flat"] = (pick == answer, probs.get(answer, 0.0))

    for name, ordered in (("sem8", sorted(words, key=SEM.get)), ("freq8", list(words))):
        pool, p_total, ok, depth = ordered, 1.0, True, 0
        while len(pool) > 8:
            groups = chunks(pool)
            crit = {f"g{i}": "One of these words: " + ", ".join(g) for i, g in enumerate(groups)}
            pick, probs = ask(state, head + "Which group contains the word that belongs in the marked slot?", crit)
            true_g = next(i for i, g in enumerate(groups) if answer in g)
            p_total *= probs.get(f"g{true_g}", 0.0)
            ok = ok and pick == f"g{true_g}"
            pool = groups[true_g]  # follow the true path so every level is measured
            depth += 1
        pick, probs = ask(state, head + "Exactly which word belongs in the marked slot?", {w: None for w in pool})
        p_total *= probs.get(answer, 0.0)
        out[name] = (ok and pick == answer, p_total, depth + 1)
    return out

with ThreadPoolExecutor(8) as ex:
    results = [r for r in ex.map(run, CASES) if r]

print(f"{'answer':12} {'leaf':>4}  {'flat':>11}  {'sem 8-way':>11}  {'freq 8-way':>11}")
for r in results:
    f = lambda t: f"{'Y' if t[0] else '.'} {t[1]:.2f}"
    print(f"{r['answer']:12} {r['n']:>4}  {f(r['flat']):>11}  {f(r['sem8']):>11}  {f(r['freq8']):>11}")
n = len(results)
for k in ("flat", "sem8", "freq8"):
    acc = sum(r[k][0] for r in results) / n
    gm = math.exp(sum(math.log(max(r[k][1], 1e-6)) for r in results) / n)
    print(f"{k:6} top-1 {acc:.0%}   mean P(true word) {sum(r[k][1] for r in results)/n:.2f}   geometric mean {gm:.3f}")
print("rounds per word (8-way):", sorted({r['sem8'][2] for r in results}))
