"""Where do word errors come from: routing (kind -> area) or the final pick?"""
import sys, importlib.util
import pathlib; ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from concurrent.futures import ThreadPoolExecutor
from typesafe_sdk import Choice, TypeSafeClient
from jevchat.composer import load_lexicon, WRITING, a_kind

client = TypeSafeClient()
tree = load_lexicon()["tree"]
src = open(ROOT / "experiments" / "options_8_vs_flat.py", encoding="utf-8").read()
CASES = eval(src[src.index("CASES = [") + 8: src.index("]\n\ndef semantic_key") + 1])

def ask(state, instructions, criteria):
    a = client.system_one(state=state, questions={"q": Choice(instructions=instructions, criteria=criteria)}).choices["q"]
    return dict(a.probabilities)

def run(case):
    user, reply, answer = case
    homes = [(n["key"], leaf["key"] if "children" in n else None)
             for n in tree for leaf in n.get("children", [n]) if answer in leaf["words"]]
    if not homes:
        return {"answer": answer, "missing": True}
    state = {"conversation_so_far": [], "user_message": user}
    head = WRITING + f'Here is the reply so far, with the slot in question marked [?]: "{reply}". '
    kp = ask(state, head + "What has to go in the marked slot so that the whole reply becomes natural, grammatical English that responds to the user?",
             {n["key"]: n["desc"] for n in tree})
    ranked = sorted(kp, key=kp.get, reverse=True)
    true_kinds = {k for k, _ in homes}
    kind_rank = min(ranked.index(k) for k in true_kinds) + 1
    area_rank = None
    for k, area in homes:
        if area is None:
            continue
        node = next(n for n in tree if n["key"] == k)
        ap = ask(state, head + f"Suppose the marked slot holds {a_kind(k)}. Which area of meaning does that word belong to?",
                 {c["key"]: c["desc"] for c in node["children"]})
        r = sorted(ap, key=ap.get, reverse=True).index(area) + 1
        area_rank = r if area_rank is None else min(area_rank, r)
    return {"answer": answer, "kind_rank": kind_rank, "area_rank": area_rank, "homes": homes}

with ThreadPoolExecutor(8) as ex:
    results = list(ex.map(run, CASES))
missing = [r["answer"] for r in results if r.get("missing")]
rs = [r for r in results if not r.get("missing")]
for r in rs:
    print(f"{r['answer']:12} kind rank {r['kind_rank']}   area rank {r['area_rank'] or '-'}   {r['homes']}")
n = len(rs)
print(f"\nnot in dictionary at all: {len(missing)}/{len(results)}  {missing}")
print(f"kind: top-1 {sum(r['kind_rank']==1 for r in rs)/n:.0%}, top-3 {sum(r['kind_rank']<=3 for r in rs)/n:.0%}")
ar = [r for r in rs if r["area_rank"]]
print(f"area (nouns/verbs, n={len(ar)}): top-1 {sum(r['area_rank']==1 for r in ar)/len(ar):.0%}, top-3 {sum(r['area_rank']<=3 for r in ar)/len(ar):.0%}")
reach = sum(r["kind_rank"] <= 3 and (r["area_rank"] or 1) == 1 for r in rs)
print(f"true leaf reached by the current design (kind top-3, area top-1): {reach}/{n} = {reach/n:.0%}")
