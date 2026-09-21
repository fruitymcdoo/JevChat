"""Builds a reply out of decisions, one word at a time, left to right.

Every measurement in experiments/ points the same way: Jev is very good at picking
a whole word when it can see a real sentence (95% from a flat list of 255), and weak
when the choice is removed from that (blank neighbours, abstract categories, letters).
Writing left to right means every choice sees the real text so far.

  1. plan      one request: what the reply should do, its tone, and how many words.
  2. per word  four waves:
                 kind     what kind of word comes next (or punctuation, or stop);
                          the top few kinds all go on
                 heats    each big kind is several flat lists of 255 words; every
                          list is asked at once and sends its best few to a final
                 finals   one flat choice per kind among the heat winners
                 compare  the nominees shown as whole texts; Jev picks the one that
                          reads best, or stops, or takes back the last word
  3. assess    Jev judges the finished reply against the goal. If it falls short,
               the word Jev was least sure of is ruled out at its position and
               writing resumes from there.

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
HEAT_WINNERS = 3  # words each flat list sends to its kind's final
WORDS_PER_BRANCH = 3  # nominees taken from each kind's final
SYNONYMS = 3  # thesaurus: extra nominees for each kind's best word
MAX_UNDOS = 3  # per attempt, so going back can never loop forever
UNDO_MIN_P = 0.6  # undo only when Jev prefers it to all continuations combined, with margin
MAX_ATTEMPTS = 3  # drafts judged per reply
MAX_EMOJI = 2  # per reply, and never two in a row: they don't count as words, so they need their own limit
GRAMMAR_MIN = 0.5  # a reply must also read as correct English; `responds` alone passes broken grammar

MAX_WORDS = {"1-3": 3, "4-6": 6, "7-10": 10, "10-20": 20, "20-30": 30, "30+": 40}
OPEN_KINDS = {"noun", "verb", "adjective", "adverb", "social_word", "echo", "emoji"}  # not worth saying twice

WRITING = TASK_CONTEXT + "The reply is written one word at a time, from left to right. "


def view(tokens: list[str]) -> str:
    if not tokens:
        return "The reply has not started yet, so we are choosing its first word. "
    return f'Here is the reply so far: "{render(tokens)}". We are choosing what comes right after it. '


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
                    trace: list, banned: set[str], can_undo: bool, at_limit: bool = False,
                    wants_emoji: bool = True) -> tuple[str, float]:
        """Returns (a token, or the END / UNDO sentinel) and how decisively it won."""
        last = tokens[-1] if tokens else None
        after_punct = last is None or last in SENTENCE_END or last == "," or last in self.emoji
        here = WRITING + view(tokens)

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
        branches = {k: probs[k] for k in ranked[:BRANCHES] if k == ranked[0] or probs[k] >= MIN_BRANCH_P}

        def allowed(k: str, words: list[str]) -> list[str]:
            used = (set(tokens) if k in OPEN_KINDS else {last}) | banned
            return [w for w in words if w not in used]

        def ask_word(k: str, options: list[str]) -> Choice:
            return Choice(
                instructions=here + f"Suppose the next word is {a_kind(k)}. Exactly which word is it?" + WORD_HINT,
                criteria={w: self.nodes.get(k, {}).get("glosses", {}).get(w) for w in options},
            )

        # wave 2: heats, for every big kind among the branches, every list at once
        questions = {
            f"{k}:{j}": ask_word(k, allowed(k, words))
            for k in branches if k in self.nodes and len(self.nodes[k]["lists"]) > 1
            for j, words in enumerate(self.nodes[k]["lists"]) if allowed(k, words)
        }
        finalists: dict[str, list[str]] = {}
        for qid, d in self._wave("heats", state, questions, trace).items():
            top = sorted(d.probabilities, key=d.probabilities.get, reverse=True)[:HEAT_WINNERS]
            finalists.setdefault(qid.split(":")[0], []).extend(top)

        # wave 3: finals, one flat choice per kind
        questions = {}
        for k in branches:
            if k == "END":
                continue
            if k == "punctuation":
                questions[k] = Choice(instructions=here + "Suppose a punctuation mark comes next. Which one is it?",
                                      criteria={p: d for p, (s, d) in PUNCTUATION.items() if s not in banned})
                continue
            options = allowed(k, echo) if k == "echo" else finalists.get(k) or allowed(k, self.nodes[k]["lists"][0])
            if options:
                questions[k] = ask_word(k, list(dict.fromkeys(options)))
        nominees: dict[str, None] = {}  # ordered set of candidate tokens
        for k, d in self._wave("finals", state, questions, trace).items():
            top = sorted(d.probabilities, key=d.probabilities.get, reverse=True)[:WORDS_PER_BRANCH]
            for w in top:
                nominees.setdefault(PUNCTUATION[w][0] if k == "punctuation" else w)
            if thesaurus and k not in ("echo", "punctuation"):  # thesaurus option: widen the field
                for syn in self.thesaurus.get(top[0], [])[:SYNONYMS]:
                    if syn not in tokens and syn not in banned:
                        nominees.setdefault(syn)

        # wave 4: compare whole texts
        candidates = {f"c{i}": tok for i, tok in enumerate(nominees)}
        criteria = {cid: f'"{render(tokens + [tok])}"' for cid, tok in candidates.items()}
        if "END" in branches:
            criteria["END"] = f'Stop here. The finished reply is exactly: "{render(tokens)}"'
        if can_undo:
            criteria["UNDO"] = (
                f'Go back. The last word "{last}" was a mistake and none of the options above can rescue it. '
                f'Delete it, leaving: "{render(tokens[:-1])}"'
            )
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

    # -- one turn -----------------------------------------------------------

    def reply(self, history: list[dict], user_message: str, thesaurus: bool = True,
              threshold: float = ACCEPT_THRESHOLD) -> Iterator[dict]:
        """Yields events: plan, token / undo / end, assess, rewind, and finally done."""
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

        # --- 2. write, assess, and if rejected rewind and resume -------------
        seen: dict[str, str] = {}
        for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", user_message):
            seen.setdefault(w.lower(), w if w[1:].islower() and len(w) > 1 else w.lower())  # keep "Australia", not "HEY"
        echo = list(seen.values())[:255]

        tokens: list[str] = []
        strength: list[float] = []  # how decisively each token won its comparison
        banned: dict[tuple, set[str]] = {}  # text before a position -> words rejected there (by undo or rewind)
        best = {"score": -1.0, "text": "..."}
        accepted = False
        attempt = 0
        while True:
            attempt += 1
            undos_left = MAX_UNDOS
            while True:  # the word loop
                n_words = sum(t not in SENTENCE_END and t != "," and t not in self.emoji for t in tokens)  # emoji are free
                force_end = n_words >= max_words and not self._ended(tokens)
                n_emoji = sum(t in self.emoji for t in tokens)
                may_emoji = picks.get("emoji") == "yes" and n_emoji < MAX_EMOJI and not (tokens and tokens[-1] in self.emoji)
                at_limit = n_words >= max_words and not force_end  # out of words, sentence closed: an emoji or nothing
                if at_limit and not may_emoji:
                    break
                if len(tokens) >= max_words * 2 + MAX_EMOJI:  # backstop; punctuation is free too
                    break
                state = {"conversation_so_far": recent, "user_message": user_message,
                         "reply_plan": plan, "reply_so_far": render(tokens)}
                trace = []
                token, p = self._next_token(state, tokens, echo, force_end, thesaurus, trace,
                                            banned=banned.get(tuple(tokens), set()),
                                            can_undo=bool(tokens) and undos_left > 0 and not force_end and not at_limit,
                                            at_limit=at_limit, wants_emoji=may_emoji)
                tally(trace)
                if token == "END":
                    yield {"type": "end", "trace": trace}
                    break
                if token == "UNDO":
                    undos_left -= 1
                    removed = tokens.pop()
                    strength.pop()
                    banned.setdefault(tuple(tokens), set()).add(removed)  # don't walk into the same mistake
                    yield {"type": "undo", "token": removed, "text": render(tokens), "trace": trace}
                    continue
                tokens.append(token)
                strength.append(p)
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

            # Rejected: rewind to the word Jev was least sure of and rule it out there.
            # (Closing marks are skipped: swapping "." for "!" doesn't change what was said.)
            words_at = [i for i, t in enumerate(tokens) if t not in SENTENCE_END] or list(range(len(tokens)))
            weakest = min(words_at, key=strength.__getitem__)
            banned.setdefault(tuple(tokens[:weakest]), set()).add(tokens[weakest])
            yield {"type": "rewind", "token": tokens[weakest], "strength": round(strength[weakest], 4),
                   "text": render(tokens[:weakest])}
            del tokens[weakest:], strength[weakest:]

        yield {"type": "done", "text": best["text"], "score": max(best.get("responds", 0.0), 0.0), "threshold": threshold,
               "accepted": accepted, "attempts": attempt, **totals}
