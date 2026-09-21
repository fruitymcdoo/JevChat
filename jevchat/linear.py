"""Builds a reply out of decisions, one word at a time, left to right.

Every measurement in experiments/ points the same way: Jev is very good at picking
a whole word when it can see a real sentence (95% from a flat list of 255), and weak
when the choice is removed from that (blank neighbours, abstract categories, letters).
Writing left to right means every choice sees the real text so far.

  1. plan      one request: what the reply should do, its tone, how many words, and
               whether an emoji suits it.
  2. per word  four waves:
                 kind     what kind of word comes next (or punctuation, or stop);
                          the top few kinds all go on
                 heats    each big kind is several flat lists of 255 words; every
                          list is asked at once and sends its best few to a final
                 finals   one flat choice per kind among the heat winners
                 compare  the nominees shown as whole texts; Jev picks the one that
                          reads best, or stops, or goes back
  3. go back   when Jev chooses to go back it also chooses how far: one word, a few,
               the sentence, or everything. Nothing is banned afterwards. Jev is simply
               told what was tried there and taken back, and writes on freely.
  4. assess    Jev judges the finished reply against the goal. If it falls short, Jev
               chooses how far to go back and writing resumes.

Code owns the dictionary, the loop, the limits and the typography. Jev owns every
judgment about what to say.
"""

from __future__ import annotations

import re
from typing import Iterator

from typesafe_sdk import Choice, Noul

from .composer import (ACCEPT_THRESHOLD, ASSESS, GATE, HISTORY_TURNS, PLAN, PUNCTUATION, SENTENCE_END,
                       TASK_CONTEXT, WORD_HINT, a_kind, load_lexicon, render)
from .decider import Decider, decide_wave

BRANCHES = 3  # top kinds of word that go on to the word search
MIN_BRANCH_P = 0.10  # ...if at least this probable
MIN_BIG_BRANCH_P = 0.20  # a big kind (nouns: 40 lists) costs far more to search, so it has to be likelier
HEAT_WINNERS = 3  # words each flat list sends to its kind's final
WORDS_PER_BRANCH = 3  # nominees taken from each kind's final
SYNONYMS = 3  # thesaurus: extra nominees for each kind's best word
MAX_UNDOS = 3  # times per attempt Jev may choose to go back, so it can never loop forever
UNDO_MIN_P = 0.6  # go back only when Jev prefers it to all continuations combined, with margin
BACK_STEPS = (1, 2, 3, 4, 6, 8)  # how many words Jev may take back at once (plus: the sentence, everything)
MAX_ATTEMPTS = 3  # drafts judged per reply
MAX_EMOJI = 2  # per reply, and never two in a row: they don't count as words, so they need their own limit
GRAMMAR_MIN = 0.25  # a floor, not a bar: long replies with a slip score 0.3-0.6, truly broken text under 0.1

MAX_WORDS = {"1-3": 3, "4-6": 6, "7-10": 10, "10-20": 20, "20-30": 30, "30+": 40}

WRITING = TASK_CONTEXT + "The reply is written one word at a time, from left to right. "


def view(tokens: list[str], taken_back: list[str] = ()) -> str:
    if not tokens:
        text = "The reply has not started yet, so we are choosing its first word. "
    else:
        text = f'Here is the reply so far: "{render(tokens)}". We are choosing what comes right after it. '
    if taken_back:
        tried = " and ".join(f'"{t}"' for t in taken_back[-3:])
        text += f"We already tried continuing from here with {tried} and took it back, so the reply needs to go a different way. "
    return text


class LinearComposer:
    def __init__(self, decider: Decider, lexicon: dict | None = None):
        self.decider = decider
        lexicon = lexicon or load_lexicon()
        self.nodes = {n["key"]: n for n in lexicon["tree"]}
        self.thesaurus: dict[str, list[str]] = lexicon["thesaurus"]
        self.emoji = {w for l in self.nodes.get("emoji", {}).get("lists", []) for w in l}

    def _ended(self, tokens: list[str]) -> bool:
        """Does the text close a sentence? An emoji may trail the closing mark: "Congratulations! 🎉"."""
        spoken = [t for t in tokens if t not in self.emoji]
        return bool(spoken) and spoken[-1] in SENTENCE_END

    def _wave(self, stage: str, state: dict, questions: dict, trace: list, labels: dict | None = None) -> dict:
        if not questions:
            return {}
        batch = decide_wave(self.decider, stage, state, questions)
        trace.append(batch.to_json(labels=labels))
        return batch.decisions

    # -- one word -----------------------------------------------------------

    def _next_token(self, state: dict, tokens: list[str], echo: list[str], force_end: bool, thesaurus: bool,
                    trace: list, taken_back: list[str], heats_cache: dict, can_undo: bool,
                    at_limit: bool = False, wants_emoji: bool = True) -> tuple[str, float]:
        """Returns (a token, or the END / UNDO sentinel) and how decisively it won."""
        last = tokens[-1] if tokens else None
        after_punct = last is None or last in SENTENCE_END or last == "," or last in self.emoji
        here = WRITING + view(tokens, taken_back)

        if force_end:  # out of words: only let Jev choose how the sentence closes
            marks = {k: d for k, (s, d) in PUNCTUATION.items() if s in SENTENCE_END}
            q = Choice(instructions=here + "The reply has to end now. Which punctuation mark should close it?", criteria=marks)
            return PUNCTUATION[self._wave("closing mark", state, {"mark": q}, trace)["mark"].value][0], 1.0

        # wave 1: kind of word. The top few all go on, not just the winner.
        kinds = {k: n["desc"] for k, n in self.nodes.items() if k != "emoji" or wants_emoji}
        if echo:
            kinds["echo"] = "A word repeated from the user's own message, because the reply has to name the very same thing, such as a name."
        if not after_punct:
            kinds["punctuation"] = "A punctuation mark (. ? ! ,), because the phrase or sentence written so far is complete."
        if self._ended(tokens):
            kinds["END"] = "Nothing. The reply so far is already a complete reply and should stop here."
        if at_limit:
            kinds = {k: d for k, d in kinds.items() if k in ("emoji", "END")}
        q = Choice(
            instructions=here + "What has to come next so that the reply grows into natural, grammatical English that responds to the user?",
            criteria=kinds,
        )
        probs = self._wave("kind of word", state, {"kind": q}, trace)["kind"].probabilities
        ranked = sorted(probs, key=probs.get, reverse=True)
        def floor(k: str) -> float:
            return MIN_BIG_BRANCH_P if k in self.nodes and len(self.nodes[k]["lists"]) > 1 else MIN_BRANCH_P

        branches = {k: probs[k] for k in ranked[:BRANCHES] if k == ranked[0] or probs[k] >= floor(k)}

        def allowed(words: list[str]) -> list[str]:
            return [w for w in words if w != last]  # the only rule: no stammering ("the the"). Any word may come back later.

        def ask_word(k: str, options: list[str]) -> Choice:
            return Choice(
                instructions=here + f"Suppose the next word is {a_kind(k)}. Exactly which word is it?" + WORD_HINT,
                criteria={w: self.nodes.get(k, {}).get("glosses", {}).get(w) for w in options},
            )

        # wave 2: heats, for every big kind among the branches, every list at once.
        # Going back and writing forward again revisits the same text, so heat results are remembered.
        big = [k for k in branches if k in self.nodes and len(self.nodes[k]["lists"]) > 1]
        questions = {
            f"{k}:{j}": ask_word(k, allowed(words))
            for k in big if (tuple(tokens), k) not in heats_cache
            for j, words in enumerate(self.nodes[k]["lists"])
        }
        fresh: dict[str, list[str]] = {}
        for qid, d in self._wave("heats", state, questions, trace).items():
            top = sorted(d.probabilities, key=d.probabilities.get, reverse=True)[:HEAT_WINNERS]
            fresh.setdefault(qid.split(":")[0], []).extend(top)
        for k, words in fresh.items():
            heats_cache[(tuple(tokens), k)] = words
        finalists = {k: heats_cache[(tuple(tokens), k)] for k in big}

        # wave 3: finals, one flat choice per kind
        questions = {}
        for k in branches:
            if k == "END":
                continue
            if k == "punctuation":
                questions[k] = Choice(instructions=here + "Suppose a punctuation mark comes next. Which one is it?",
                                      criteria={p: d for p, (_, d) in PUNCTUATION.items()})
                continue
            options = allowed(echo) if k == "echo" else finalists.get(k) or allowed(self.nodes[k]["lists"][0])
            if options:
                questions[k] = ask_word(k, list(dict.fromkeys(options)))
        nominees: dict[str, None] = {}  # ordered set of candidate tokens
        for k, d in self._wave("finals", state, questions, trace).items():
            top = sorted(d.probabilities, key=d.probabilities.get, reverse=True)[:WORDS_PER_BRANCH]
            for w in top:
                nominees.setdefault(PUNCTUATION[w][0] if k == "punctuation" else w)
            if thesaurus and k not in ("echo", "punctuation"):  # thesaurus option: widen the field
                for syn in self.thesaurus.get(top[0], [])[:SYNONYMS]:
                    if syn != last:
                        nominees.setdefault(syn)

        # wave 4: compare whole texts
        candidates = {f"c{i}": tok for i, tok in enumerate(nominees)}
        criteria = {cid: f'"{render(tokens + [tok])}"' for cid, tok in candidates.items()}
        if "END" in branches:
            criteria["END"] = f'Stop here. The finished reply is exactly: "{render(tokens)}"'
        if can_undo:
            criteria["UNDO"] = ("Go back. The reply has gone wrong and none of the options above can rescue it, "
                                "so some of the last words should be deleted.")
        if not criteria:
            return "END", 1.0
        if len(criteria) == 1:
            pick, p = next(iter(criteria)), 1.0
        else:
            q = Choice(
                instructions=WRITING + "Each option shows how the reply would read after adding one more word. Which option is "
                "the most grammatical, natural English and the best start toward a good reply to the user?",
                criteria=criteria,
            )
            labels = {cid: f"+ {tok}" for cid, tok in candidates.items()}
            probs = self._wave("compare texts", state, {"text": q}, trace, labels)["text"].probabilities
            ranked = sorted(probs, key=probs.get, reverse=True)
            pick = ranked[0]
            if pick == "UNDO" and probs["UNDO"] < UNDO_MIN_P and len(ranked) > 1:
                pick = ranked[1]  # going back needs conviction, not a plurality
            p = probs[pick]
        return candidates.get(pick, pick), p

    # -- going back -----------------------------------------------------------

    def _how_far_back(self, state: dict, tokens: list[str], why: str, trace: list) -> int:
        """Jev decides how many tokens to delete from the end. Returns at least 1."""
        cuts: dict[int, str] = {}  # tokens removed -> description
        spoken = [i for i, t in enumerate(tokens) if t not in SENTENCE_END and t != "," and t not in self.emoji]
        for n in BACK_STEPS:  # n counts words; punctuation and emoji after the cut go with them
            if n < len(spoken):
                cut = len(tokens) - spoken[-n]
                cuts.setdefault(cut, f"Take back the last {n} word{'s' if n > 1 else ''}")
        ends = [i for i, t in enumerate(tokens[:-1]) if t in SENTENCE_END]
        if ends and len(tokens) - ends[-1] - 1 not in cuts:
            cuts[len(tokens) - ends[-1] - 1] = "Take back the whole last sentence"
        cuts.setdefault(len(tokens), "Take back everything and start the reply again")
        if len(cuts) == 1:
            return next(iter(cuts))
        criteria = {
            f"back{cut}": desc + (f', leaving: "{render(tokens[:-cut])}"' if cut < len(tokens) else ", leaving nothing.")
            for cut, desc in sorted(cuts.items())
        }
        q = Choice(
            instructions=WRITING + f'Here is the reply so far: "{render(tokens)}". ' + why
            + " How much of it should be deleted, so that what is left is a good start that can be continued into a good reply? "
            "Delete as little as possible, but everything that has to go.",
            criteria=criteria,
        )
        pick = self._wave("how far back", state, {"back": q}, trace)["back"].value
        return int(pick.removeprefix("back"))

    # -- one turn -----------------------------------------------------------

    def reply(self, history: list[dict], user_message: str, thesaurus: bool = True,
              threshold: float = ACCEPT_THRESHOLD) -> Iterator[dict]:
        """Yields events: plan, token / back / end, assess, and finally done."""
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
        max_words = MAX_WORDS.get(picks.get("length"), 10)
        yield {"type": "plan", "plan": picks, "max_words": max_words, "trace": trace}

        # --- 2. write, assess, and if rejected go back and resume ------------
        seen: dict[str, str] = {}
        for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", user_message):
            seen.setdefault(w.lower(), w if w[1:].islower() and len(w) > 1 else w.lower())  # keep "Australia", not "HEY"
        echo = list(seen.values())[:255]

        tokens: list[str] = []
        taken_back: dict[tuple, list[str]] = {}  # text before a position -> continuations tried there and deleted
        heats_cache: dict = {}
        best = {"score": -1.0, "text": "..."}
        accepted = False
        attempt = 0

        def go_back(why: str, trace: list) -> dict:
            cut = self._how_far_back(state, tokens, why, trace)
            removed = tokens[-cut:]
            del tokens[-cut:]
            taken_back.setdefault(tuple(tokens), []).append(render(removed).rstrip(".").lower() or render(removed))
            return {"type": "back", "removed": render(removed), "text": render(tokens), "trace": trace}

        while True:
            attempt += 1
            undos_left = MAX_UNDOS
            while True:  # the word loop
                n_words = sum(t not in SENTENCE_END and t != "," and t not in self.emoji for t in tokens)  # emoji are free
                force_end = n_words >= max_words and not self._ended(tokens)
                n_emoji = sum(t in self.emoji for t in tokens)
                may_emoji = (picks.get("emoji") == "yes" and n_emoji < MAX_EMOJI and tokens[-1:] != [] and tokens[-1] in SENTENCE_END)  # only right after a closing mark: mid-sentence emoji read as broken grammar
                at_limit = n_words >= max_words and not force_end  # out of words, sentence closed: an emoji or nothing
                if at_limit and not may_emoji:
                    break
                if len(tokens) >= max_words * 2 + MAX_EMOJI:  # backstop; punctuation is free too
                    break
                state = {"conversation_so_far": recent, "user_message": user_message,
                         "reply_plan": plan, "reply_so_far": render(tokens)}
                trace = []
                token, _ = self._next_token(state, tokens, echo, force_end, thesaurus, trace,
                                            taken_back=taken_back.get(tuple(tokens), []), heats_cache=heats_cache,
                                            can_undo=bool(tokens) and undos_left > 0 and not force_end and not at_limit,
                                            at_limit=at_limit, wants_emoji=may_emoji)
                if token == "END":
                    tally(trace)
                    yield {"type": "end", "trace": trace}
                    break
                if token == "UNDO":
                    undos_left -= 1
                    event = go_back("The reply has gone wrong.", trace)
                    tally(trace)
                    yield event
                    continue
                tally(trace)
                tokens.append(token)
                yield {"type": "token", "token": token, "text": render(tokens), "trace": trace}
            if not tokens:
                break

            # --- 3. assess ---------------------------------------------------
            text = render(tokens)
            trace = []
            judged = {"conversation_so_far": recent, "user_message": user_message, "reply": text}  # no plan: judge the result, not the intent
            decisions = self._wave("assess", judged, {k: Noul(instructions=i) for k, i in ASSESS.items()}, trace)
            tally(trace)
            scores = {k: round(d.value, 4) for k, d in decisions.items()}
            score = scores[GATE]
            accepted = score >= threshold and scores["grammatical"] >= GRAMMAR_MIN
            ranking = score if scores["grammatical"] >= GRAMMAR_MIN else score * scores["grammatical"]  # broken grammar can't be "best"
            if ranking > best["score"]:
                best = {"score": ranking, "text": text, "responds": score}
            yield {"type": "assess", "attempt": attempt, "text": text, "scores": scores, "score": score,
                   "threshold": threshold, "accepted": accepted, "trace": trace}
            if accepted or attempt >= MAX_ATTEMPTS:
                break

            # Rejected: Jev decides how much of it to take back, then writes on from there.
            trace = []
            event = go_back("This finished reply was judged not good enough as a reply to the user.", trace)
            tally(trace)
            yield event

        yield {"type": "done", "text": best["text"], "score": max(best.get("responds", 0.0), 0.0), "threshold": threshold,
               "accepted": accepted, "attempts": attempt, **totals}
