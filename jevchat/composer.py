"""Builds a reply out of decisions, all words in parallel.

There is no canned text. The reply is a row of numbered slots, each holding one
word or punctuation mark (or nothing). Jev never writes; it answers questions
about slots, and every slot is asked about at the same time.

  1. plan      one request: what the reply should do, its tone, and how many
               words it should contain. The word count fixes the layout: how
               many sentences, and how many slots in each. Sentences are
               written one at a time, each seeing the finished ones before it,
               because parallel filling is sharp up to about nine slots.
  2. fill      rounds, each costing the same three request-waves however
               long the reply is. In a round every open slot at once descends
               the dictionary (kind of word -> heats across that kind's
               flat word lists -> a final among the winners), seeing the draft so far with open slots shown as blanks.
               That gives each slot a handful of candidates with probabilities:
               its "potentials". Asked blind, slots in the middle of a sentence
               give blurry answers while the edges are sharp, so each round
               locks in only the slots Jev is surest about (never two
               neighbours at once) and the rest are asked again with those
               anchors in view.
  3. settle    every slot at once compares whole-text versions of the reply,
               one per candidate for that slot. Odd slots first, then even, so
               neighbours don't change under each other's feet.
  4. assess    Jev judges the draft against the goal. If it falls short, the
               shakiest slots are blanked and filled again.

Code owns the tree, the slots, the rounds and the typography. Jev owns every
judgment about what to say.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from typesafe_sdk import Choice, Noul

from .decider import Decider, decide_wave

HISTORY_TURNS = 6  # send only recent context; extra state distracts the model

BRANCHES = 3  # top kinds of word each slot descends
MIN_BRANCH_P = 0.15  # ...if at least this probable
FOCUS_SLOTS = 3  # slots per round that get the expensive word search (cheap, small-list slots always do)
HEAT_WINNERS = 3  # words each flat list sends to its kind's final
WORDS_PER_BRANCH = 3  # nominees taken from each branch's final
SYNONYMS = 3  # thesaurus: extra nominees for each branch's best word
SYNONYM_DISCOUNT = 0.5  # synonyms weren't scored themselves; they inherit a share of their source's probability
CANDIDATES = 6  # potentials kept per slot
COMMIT_FRACTION = 0.5  # each round locks in at most this share of the open slots, surest first (never two neighbours at once)
COMMIT_MIN_P = 0.30  # ...and only slots at least this sure, though always at least the single surest one
MAX_ROUNDS = 10  # nominate rounds per sentence, across all its attempts
MAX_ATTEMPTS = 3  # drafts judged per sentence
REOPEN_FRACTION = 0.34  # after a rejection, this share of slots (the shakiest) is blanked and refilled

# The goal. A draft is judged by Jev; only GATE decides acceptance, the others
# are diagnostics that ride along in the same request.
ACCEPT_THRESHOLD = 0.80  # default; the UI can override per message. Good replies to complex messages tend to land around 0.80-0.90.
GATE = "responds"
ASSESS = {
    "responds": (
        "Is this a good reply to what the user said? A good reply speaks to what the user actually said, "
        "in a way a thoughtful person would find fitting and sensible."
    ),
    "grammatical": "Is the reply written in correct, natural English, with no missing or misplaced words?",
    "complete": "Does the reply end properly as a finished thought, instead of being cut off in the middle or trailing off into nonsense?",
}

PLAN = {
    "move": (
        "Think about the best possible reply to the user's message. What should that reply do?",
        {
            "greet": "Greet the user back.",
            "answer": "Answer the user's question directly.",
            "ask_back": "Ask the user a follow-up question about what they said.",
            "sympathize": "Show sympathy or comfort about something bad that happened to the user.",
            "celebrate": "Share the user's joy about something good that happened to them.",
            "acknowledge": "Briefly acknowledge or agree with what the user said.",
            "opinion": "Give the assistant's own opinion or preference.",
            "accept_thanks": "Respond graciously to the user's thanks.",
            "farewell": "Say goodbye.",
            "decline": "Politely say that the assistant can't do that or doesn't know.",
            "clarify": "Ask the user to explain, because their message is unclear.",
        },
    ),
    "tone": (
        "Think about the best possible reply to the user's message. What tone should that reply have?",
        {
            "warm": "Warm and friendly.",
            "playful": "Playful and light.",
            "neutral": "Plain and matter-of-fact.",
            "gentle": "Gentle and caring.",
            "apologetic": "Apologetic.",
        },
    ),
    "length": (
        "How many words should an ideal response to this query contain?",
        {
            "1-3": "One to three words.",
            "4-6": "Four to six words.",
            "7-10": "Seven to ten words.",
            "10-20": "Ten to twenty words.",
            "20-30": "Twenty to thirty words.",
            "30+": "More than thirty words.",
        },
    ),
}
PLAN["emoji"] = (
    "Think about the best possible reply to the user's message. Would it include an emoji?",
    {
        "yes": "Yes. The reply should end with one fitting emoji, as a friendly person would in a casual chat.",
        "no": "No. Plain words suit this reply better, because the topic is serious, factual or formal.",
    },
)

# How each answer is laid out: (sentences, words per sentence). Parallel slot filling is
# sharp up to about nine slots and blurs beyond that, so longer replies are written one
# sentence at a time, each seeing the finished sentences before it.
LAYOUT = {"1-3": (1, 3), "4-6": (1, 5), "7-10": (1, 8), "10-20": (2, 7), "20-30": (3, 8), "30+": (4, 9)}


def slot_count(words: int) -> int:
    """Room for the words plus the closing mark."""
    return words + 1


PUNCTUATION = {
    "period": (".", "A period, which ends a statement."),
    "question_mark": ("?", "A question mark, which ends a question."),
    "exclamation_mark": ("!", "An exclamation mark, which ends an excited or emphatic sentence."),
    "comma": (",", "A comma, which marks a pause inside the sentence."),
}
SENTENCE_END = {".", "?", "!"}
EMPTY = ""  # a slot deliberately left without a word

# Every composing question opens with this. Measured in experiments/parrot_rate.py: saying who is
# speaking, and that a reply is not an echo, takes the comparison step's preference for parroted
# replies from 5% to 0%.
TASK_CONTEXT = (
    "This is a conversation between two people, a user and an assistant. The user has just spoken, and now it is "
    "the assistant's turn. We are writing what the assistant says back, following the plan for the reply. "
    "The assistant speaks for itself: in its reply, \"I\" means the assistant and \"you\" means the user. "
    "A good reply reacts to what the user said: it answers, sympathises, congratulates, or asks something new. "
    "It never simply says the user's own sentence back to them, because the user already knows what they said. "
)
WRITING = TASK_CONTEXT + "The reply is a row of slots, and each slot holds one word or one punctuation mark. "
# Appended to every word-picking question. With blank neighbours the user's words are the most salient
# thing in view, and Jev put 37% of its probability on them; with this hint, 8%.
WORD_HINT = (
    " The reply should bring something new, so prefer a word the user did not already use, "
    "unless the reply has to name the very same thing."
)


def a_kind(kind: str) -> str:
    """'noun' -> 'a noun', 'adjective' -> 'an adjective', 'helper_verb' -> 'a helper verb'."""
    name = kind.replace("_", " ")
    return ("an " if name[0] in "aeiou" else "a ") + name


def load_lexicon(path: Path | None = None) -> dict:
    path = path or Path(__file__).with_name("lexicon.json")
    return json.loads(path.read_text(encoding="utf-8"))


def render(tokens: list[str | None]) -> str:
    """Typography is code's job: spacing, capitals, the pronoun I. Empty slots vanish."""
    text = ""
    capitalize = True
    for tok in tokens:
        if not tok:
            continue
        if tok in SENTENCE_END or tok == ",":
            text += tok
            capitalize = capitalize or tok in SENTENCE_END
            continue
        if tok == "i" or tok.startswith("i'"):
            tok = "I" + tok[1:]
        if capitalize:
            tok = tok[0].upper() + tok[1:]
            capitalize = False
        text += (" " if text else "") + tok
    return text


def view(slots: list[str | None], i: int, prefix: list[str] = ()) -> str:
    """The draft as Jev sees it when asked about slot i. `prefix` holds the sentences already finished."""
    parts = list(prefix)
    for j, tok in enumerate(slots):
        if j == i:
            parts.append("[?]")
        elif tok is None:
            parts.append("___")
        elif tok != EMPTY:
            parts.append(tok)
    shown = " ".join(parts)
    return (
        f'Here is the reply so far, with the slot in question marked [?] and slots not yet filled shown as ___: "{shown}". '
        f"The marked slot is slot {i + 1} of the {len(slots)} slots in the sentence being written. "
    )


class Composer:
    def __init__(self, decider: Decider, lexicon: dict | None = None):
        self.decider = decider
        lexicon = lexicon or load_lexicon()
        self.tree: list[dict] = lexicon["tree"]
        self.nodes = {n["key"]: n for n in self.tree}
        self.thesaurus: dict[str, list[str]] = lexicon["thesaurus"]

    def _wave(self, stage: str, state: dict, questions: dict, trace: list, labels: dict | None = None) -> dict:
        if not questions:
            return {}
        batch = decide_wave(self.decider, stage, state, questions)
        trace.append(batch.to_json(labels=labels))
        return batch.decisions

    # -- nominate: every slot descends the tree at once ---------------------

    def _nominate(self, state: dict, slots: list, targets: list[int], echo: list[str], thesaurus: bool,
                  trace: list, prefix: list[str]) -> dict[int, dict[str, float]]:
        """Returns, for each target slot, its potentials: candidate token -> probability."""
        # wave 1: kind of word, for every target slot
        questions = {}
        for i in targets:
            kinds = {k: node["desc"] for k, node in self.nodes.items()}
            if echo:
                kinds["echo"] = "A word repeated from the user's own message, because the reply has to name the very same thing, such as a name."
            if i > 0 or prefix:
                kinds["EMPTY"] = "Nothing. The reply reads best with no word in this slot, or the sentence has already ended before it."
            if i > 0:
                kinds["punctuation"] = "A punctuation mark (. ? ! ,), because the phrase or sentence before this slot is complete."
            questions[str(i)] = Choice(
                instructions=WRITING + view(slots, i, prefix)
                + "What has to go in the marked slot so that the whole reply becomes natural, grammatical English that responds to the user?",
                criteria=kinds,
            )
        kind_p: dict[int, dict[str, float]] = {}
        for qid, d in self._wave("nominate: kind of word", state, questions, trace).items():
            ranked = sorted(d.probabilities, key=d.probabilities.get, reverse=True)
            kind_p[int(qid)] = {k: d.probabilities[k] for k in ranked[:BRANCHES] if k == ranked[0] or d.probabilities[k] >= MIN_BRANCH_P}

        # Heats are the expensive part (a question per 255-word list), and only a slot or two locks per
        # round anyway. So only the slots whose kind Jev is surest of go on; the rest wait for a later round.
        costly = sorted((i for i, branches in kind_p.items()
                         if any(k in self.nodes and len(self.nodes[k]["lists"]) > 1 for k in branches)),
                        key=lambda i: -max(kind_p[i].values()))
        for i in costly[FOCUS_SLOTS:]:
            kind_p.pop(i)

        # wave 2: heats. A big kind (nouns, verbs...) is several flat lists of up to 255 words;
        # every list is asked at once and sends its best few words through to the final.
        def ask_word(i: int, k: str, options: list[str]) -> Choice:
            return Choice(
                instructions=WRITING + view(slots, i, prefix) + f"Suppose the marked slot holds {a_kind(k)}. Exactly which word is it?" + WORD_HINT,
                criteria={w: self.nodes.get(k, {}).get("glosses", {}).get(w) for w in options},
            )

        questions = {
            f"{i}:{k}:{j}": ask_word(i, k, words)
            for i, branches in kind_p.items() for k in branches
            if k in self.nodes and len(self.nodes[k]["lists"]) > 1
            for j, words in enumerate(self.nodes[k]["lists"])
        }
        finalists: dict[tuple[int, str], list[str]] = {}
        for qid, d in self._wave("nominate: heats", state, questions, trace).items():
            i, k, _ = qid.split(":")
            top = sorted(d.probabilities, key=d.probabilities.get, reverse=True)[:HEAT_WINNERS]
            finalists.setdefault((int(i), k), []).extend(top)

        # wave 3: finals. One flat choice per branch: the heat winners, or the whole list for a small kind.
        questions = {}
        for i, branches in kind_p.items():
            for k in branches:
                if k == "EMPTY":
                    continue
                if k == "punctuation":
                    questions[f"{i}:{k}"] = Choice(
                        instructions=WRITING + view(slots, i, prefix) + "Suppose the marked slot holds a punctuation mark. Which one is it?",
                        criteria={p: d for p, (_, d) in PUNCTUATION.items()},
                    )
                    continue
                options = echo if k == "echo" else finalists.get((i, k)) or self.nodes[k]["lists"][0]
                questions[f"{i}:{k}"] = ask_word(i, k, list(dict.fromkeys(options)))
        potentials: dict[int, dict[str, float]] = {
            i: ({EMPTY: branches["EMPTY"]} if "EMPTY" in branches else {}) for i, branches in kind_p.items()
        }
        for qid, d in self._wave("nominate: finals", state, questions, trace).items():
            i, k = qid.split(":")
            i = int(i)
            top = sorted(d.probabilities, key=d.probabilities.get, reverse=True)[:WORDS_PER_BRANCH]
            for w in top:
                tok = PUNCTUATION[w][0] if k == "punctuation" else w
                p = kind_p[i][k] * d.probabilities[w]  # P(kind) x P(word | kind)
                potentials[i][tok] = max(potentials[i].get(tok, 0.0), p)
            if thesaurus and k not in ("echo", "punctuation"):  # thesaurus option: widen the field
                p = kind_p[i][k] * d.probabilities[top[0]] * SYNONYM_DISCOUNT
                for syn in self.thesaurus.get(top[0], [])[:SYNONYMS]:
                    potentials[i].setdefault(syn, p)
        return {i: dict(sorted(p.items(), key=lambda kv: -kv[1])[:CANDIDATES]) for i, p in potentials.items()}

    # -- settle: every slot compares whole texts at once --------------------

    def _settle(self, state: dict, slots: list[str], potentials: list[dict[str, float]], parity: int,
                trace: list, prefix: list[str]) -> tuple[list[str], dict[int, float]]:
        questions, options, labels = {}, {}, {}
        for i in range(parity, len(slots), 2):
            by_text: dict[str, str] = {}  # rendered reply -> token, deduped (an empty slot may render like another)
            for tok in [slots[i], *potentials[i]]:
                by_text.setdefault(render(prefix + slots[:i] + [tok] + slots[i + 1:]), tok)
            if len(by_text) < 2:
                continue
            ids = {f"s{i}c{j}": text for j, text in enumerate(by_text)}
            options[str(i)] = {cid: by_text[text] for cid, text in ids.items()}
            labels.update({cid: f"slot {i + 1}: {by_text[text] or '(empty)'}" for cid, text in ids.items()})
            questions[str(i)] = Choice(
                instructions=WRITING
                + f"Each option shows the whole reply with a different choice for slot {i + 1}. "
                "Which option is the most grammatical, natural English and the best reply to the user?",
                criteria={cid: f'"{text}"' for cid, text in ids.items()},
            )
        slots = list(slots)
        strength = {}
        stage = f"settle: {'odd' if parity == 0 else 'even'} slots"  # slots are shown 1-based
        for i, d in self._wave(stage, state, questions, trace, labels).items():
            slots[int(i)] = options[i][d.value]
            strength[int(i)] = d.probabilities[d.value]
        return slots, strength

    # -- one turn ---------------------------------------------------------

    def reply(self, history: list[dict], user_message: str, thesaurus: bool = True,
              threshold: float = ACCEPT_THRESHOLD) -> Iterator[dict]:
        """Yields events: plan, then per round draft and assess, and finally done."""
        recent = history[-HISTORY_TURNS:]
        totals = {"requests": 0, "latency_ms": 0, "input_tokens": 0}

        def tally(batches):
            for b in batches:
                totals["requests"] += b["requests"]
                totals["latency_ms"] += b["latency_ms"]
                totals["input_tokens"] += b["input_tokens"] or 0

        # --- 1. plan -------------------------------------------------------
        state = {"conversation_so_far": recent, "user_message": user_message}
        trace: list = []
        decisions = self._wave("plan", state, {qid: Choice(instructions=i, criteria=c) for qid, (i, c) in PLAN.items()}, trace)
        tally(trace)
        picks = {qid: d.value for qid, d in decisions.items()}
        plan = {qid: PLAN[qid][1][label] for qid, label in picks.items()}
        n_sentences, words_each = LAYOUT.get(picks.get("length"), (1, 8))
        n_slots = slot_count(words_each)
        yield {"type": "plan", "plan": picks, "sentences": n_sentences, "slots": n_slots, "trace": trace}

        seen: dict[str, str] = {}
        for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", user_message):
            seen.setdefault(w.lower(), w if w[1:].islower() and len(w) > 1 else w.lower())  # keep "Australia", not "HEY"
        echo = list(seen.values())[:255]
        state = {"conversation_so_far": recent, "user_message": user_message, "reply_plan": plan}

        def judge(text: str, stage: str):
            trace = []
            judged = {"conversation_so_far": recent, "user_message": user_message, "reply": text}  # no plan: judge the result, not the intent
            decisions = self._wave(stage, judged, {k: Noul(instructions=i) for k, i in ASSESS.items()}, trace)
            tally(trace)
            return {k: round(d.value, 4) for k, d in decisions.items()}, trace

        best = {"score": -1.0, "text": "..."}
        accepted = False
        prefix: list[str] = []  # tokens of the sentences already finished
        total_rounds = total_attempts = 0

        # --- 2. one sentence at a time -------------------------------------
        for sentence in range(1, n_sentences + 1):
            slots: list = [None] * n_slots  # None = still open
            potentials: list[dict[str, float]] = [{} for _ in slots]  # each slot's candidates from when it was last asked
            strength = [0.0] * n_slots  # how sure Jev was of what each slot holds
            kept_slots, kept_score = None, -1.0  # this sentence's best version
            rounds = attempt = 0

            def neighbour(i: int, step: int):
                """Nearest filled, non-empty slot to the left (-1) or right (+1)."""
                j = i + step
                while 0 <= j < n_slots:
                    if slots[j]:
                        return slots[j]
                    j += step
                return prefix[-1] if step < 0 and prefix else None

            while attempt < MAX_ATTEMPTS:
                attempt += 1
                total_attempts += 1

                # fill: each round, lock in the slots Jev is surest about
                while None in slots and rounds < MAX_ROUNDS:
                    rounds += 1
                    total_rounds += 1
                    trace = []
                    open_slots = [i for i, t in enumerate(slots) if t is None]
                    fresh = self._nominate(state, slots, open_slots, echo, thesaurus, trace, prefix)
                    tally(trace)
                    for i, pot in fresh.items():
                        potentials[i] = pot
                    last_round = rounds == MAX_ROUNDS
                    quota = len(fresh) if last_round else max(1, round(len(fresh) * COMMIT_FRACTION))
                    surest = sorted(fresh, key=lambda i: -max(fresh[i].values(), default=0.0))
                    locked = []
                    for i in surest:
                        if len(locked) >= quota:
                            break
                        if locked and not last_round and max(fresh[i].values(), default=0.0) < COMMIT_MIN_P:
                            break  # the rest are guesses; ask them again once more anchors are in view
                        if not last_round and (i - 1 in locked or i + 1 in locked):
                            continue  # neighbours were asked blind to each other; let one see the other first
                        around = {neighbour(i, -1), neighbour(i, +1)}
                        tok = next((t for t in fresh[i] if t == EMPTY or t not in around), EMPTY)  # no "you you"
                        slots[i], strength[i] = tok, fresh[i].get(tok, 0.0)
                        locked.append(i)
                    yield {"type": "draft", "sentence": sentence, "round": rounds, "text": render(prefix + slots), "trace": trace,
                           "locked": [{"slot": i + 1, "token": slots[i] or "(empty)", "p": round(strength[i], 4)} for i in sorted(locked)],
                           "view": " ".join(prefix + ["___" if t is None else t for t in slots if t != EMPTY]),
                           "potentials": {i + 1: [{"token": t or "(empty)", "p": round(p, 4)} for t, p in pot.items()] for i, pot in fresh.items()}}
                slots = [EMPTY if t is None else t for t in slots]  # out of rounds: whatever is still open stays empty

                # assess the filled draft, then let settle try to improve it
                text = render(prefix + slots)
                if not render(slots):
                    break
                scores, judge_trace = judge(text, "assess: filled draft")

                # settle: every slot compares whole texts. It can also make things worse, so the judge referees.
                trace = []
                settled, sure = list(slots), {}
                for parity in (0, 1):
                    settled, s_p = self._settle(state, settled, potentials, parity, trace, prefix)
                    sure.update(s_p)
                tally(trace)
                settled_text = render(prefix + settled)
                if render(settled) and settled_text != text:
                    settled_scores, settled_judge_trace = judge(settled_text, "assess: settled draft")
                    trace += settled_judge_trace
                    kept = settled_scores[GATE] > scores[GATE]
                    yield {"type": "settle", "attempt": attempt, "before": text, "before_score": scores[GATE],
                           "text": settled_text, "score": settled_scores[GATE], "kept": kept, "trace": trace}
                    if kept:
                        slots, text, scores = settled, settled_text, settled_scores
                        for i, p in sure.items():
                            strength[i] = p

                score = scores[GATE]
                accepted = score >= threshold
                if score > best["score"]:
                    best = {"score": score, "text": text}
                if score > kept_score:
                    kept_slots, kept_score = list(slots), score
                yield {"type": "assess", "sentence": sentence, "attempt": attempt, "text": text, "scores": scores,
                       "score": score, "threshold": threshold, "accepted": accepted, "trace": judge_trace}
                if accepted or attempt >= MAX_ATTEMPTS or rounds >= MAX_ROUNDS:
                    break

                # rejected: blank the shakiest slots and fill them again
                shaky = sorted(range(n_slots), key=strength.__getitem__)[: max(1, round(n_slots * REOPEN_FRACTION))]
                yield {"type": "reopen", "slots": [{"slot": i + 1, "token": slots[i] or "(empty)", "p": round(strength[i], 4)} for i in sorted(shaky)]}
                for i in shaky:
                    slots[i] = None

            if accepted or kept_slots is None:
                break
            prefix = prefix + [t for t in kept_slots if t]  # the next sentence builds on this one's best version

        yield {"type": "done", "text": best["text"], "score": max(best["score"], 0.0), "threshold": threshold,
               "accepted": accepted, "rounds": total_rounds, "attempts": total_attempts, **totals}
