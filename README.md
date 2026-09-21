# JevChat

A chat interface on top of [Jev](https://docs.typesafe.ai), TypeSafe's decision model.
Jev never writes text, and there is no canned text either: replies are composed from **slots filled in parallel**, each slot the result of a waterfall of typed decisions over a dictionary.
The UI shows the draft filling in round by round and every decision underneath.

## Run

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
# put TYPESAFE_API_KEY=... in .env
.venv\Scripts\python app.py           # http://localhost:5000
.venv\Scripts\python smoke_test.py -v # scripted conversation, with each slot's potentials
```

Without an API key the app falls back to a keyword-overlap `MockDecider` so the wiring still runs.

## How a reply is built

The reply is a row of numbered **slots**, each holding one word, one punctuation mark, or nothing.
Every open slot is asked about at the same time, so a round costs the same few request-waves
however long the reply is. (A wave is a set of independent questions over one state, split across
concurrent requests when it would not fit in one.)

| Stage | What happens |
| --- | --- |
| plan | 1 request: what the reply should do, its tone, and "How many words should an ideal response to this query contain?" (1-3, 4-6, 7-10, 10-20, 20-30, 30+). The answer sets the layout (`LAYOUT`): how many sentences, and how many slots in each. Sentences are written one at a time, each seeing the finished ones, because parallel filling is sharp up to about nine slots. |
| fill | Rounds of 3 waves. **Kind:** every open slot is asked what kind of word it needs. **Heats:** the few slots Jev is surest about (`FOCUS_SLOTS`) search for their word: a big kind such as nouns is 20 flat lists of 255 words by frequency, all asked at once, each sending its best 3 to a final. **Finals:** one flat choice among the heat winners gives P(word given kind); times P(kind), these are the slot's potentials. With the thesaurus on, synonyms of each branch's favourite join them. Only the surest slots are locked each round (`COMMIT_MIN_P`, never two neighbours at once); the rest are asked again with those anchors in view. |
| settle | 2 waves: every slot compares whole-text versions of the reply, one per candidate (odd slots, then even). The judge referees: the settled draft is kept only if it scores higher. |
| assess | 3 yes/no questions about the draft: `responds` (the gate), `grammatical`, `complete`. Accepted at `ACCEPT_THRESHOLD` (default 0.90, adjustable per message in the UI). |
| reopen | On rejection the shakiest third of slots is blanked and filled again, up to `MAX_ATTEMPTS` drafts and `MAX_ROUNDS` rounds. Then the best-scoring draft is sent, flagged as below the bar. |

Why lock gradually: asked blind, slots in the middle of a sentence all give the same blurry answer
("feel, feel, feel") because each imagines a different sentence, while the edges are sharp ("sorry"
first, "." last). Locking only confident slots lets a sentence grow from its anchors:
`where ___ ___ ___ ___ ?` -> `where do you ___ ___ ?` -> `Where do you go hiking?`

## Layout

- `app.py` — Flask server; `/api/chat` streams NDJSON events (`plan`, `draft`, `settle`, `assess`, `reopen`, `done`)
- `jevchat/decider.py` — the only code that talks to Jev; normalises answers into a trace
- `jevchat/composer.py` — plan, parallel slot filling, settle, assess; tuning constants at the top
- `jevchat/lexicon.json` — dictionary (~9,000 frequent words as flat lists per kind of word) and thesaurus (~5,100 entries)
- `experiments/` — known-answer measurements of Jev's accuracy at each level
- `tools/build_lexicon.py` — regenerates the lexicon from wordfreq + WordNet (`pip install nltk wordfreq`; dev-time only)
- `templates/`, `static/` — the web UI

## What we've measured

`experiments/options_8_vs_flat.py` and `experiments/routing_accuracy.py` blank one known word out
of a sensible reply and ask Jev to recover it (20 cases):

| Level | Right at top-1 |
| --- | --- |
| Kind of word (13 options) | 85% (100% within top-3) |
| WordNet meaning area (15-26 options) | 50% (75% within top-3) |
| Exact word, flat list of up to 255 | 95% |
| Exact word, via rounds of 8 word-groups | 70% |
| Exact word, spelled letter by letter (27 options a step, `experiments/spell_by_letter.py`) | 0% (0 of 27) |

Spelling fails outright: "view" came out "aaaaaa", "sun" as "sssssssss". Jev puts only 0.19 on the true
first letter and 0.29 on later ones, though it reliably knows when a correctly spelled word is complete
(about 0.9). It evidently handles words as whole units, not as letter sequences, which fits the
pattern: it is strong at picking among whole words and weak wherever a choice is removed from them.

So big flat lists are Jev's strength *when it can see the sentence*, and the meaning-area level was
the bottleneck (partly WordNet's filing: "make" and "try" sit under social verbs). That is why the
dictionary is now flat lists searched in parallel, and why it grew from 5,300 to 9,000 words.

### Why it repeated the user (`experiments/parrot_rate.py`)

| Decision point | Before | With task context | With context and a word-level hint |
| --- | --- | --- | --- |
| Whole-text comparison: probability on a reply that repeats the user | 5% (48% in one case) | 0% | 0% |
| First slot: probability of reaching for the "echo" kind | 3% | 0% | 0% |
| Picking a word for a slot with blank neighbours: probability on the user's own words (chance is about 1%) | 37% | 30% | 8% |

The copying came from the word-picking step, not from a wish to parrot: with blank neighbours the
user's words are the most salient thing in view. Explaining the task (who is speaking, what a reply
is for) fixed the comparison step but barely moved word-picking; a hint inside the word question did.
Both are now in `composer.py` (`WRITING`, `WORD_HINT`). End to end on five messages, the share of reply
words copied from the user fell in three (33% to 0%, 10% to 0%, 20% to 0%) and rose in one, and the
judge's score rose in three; that sample is too small to call more than encouraging.

## What we've seen so far

- Short and medium single sentences work: "Hi!" 89%, "I love music." 57%, "Where do you hike to?" 85%.
  New vocabulary shows up ("Congratulations", "hike", "cute").
- Two-sentence replies are still poor. Slots in the middle of a sentence are asked with blank
  neighbours, which is exactly the condition the 95% figure does not cover, and leftover slots
  collect stray punctuation: "Sorry, are you feel rough?. What do did you thing do happened?" (36%),
  "Congratulations, and really she is cute! What is the name is?" (83% on `responds`, 14% on grammar).
- Cost is high: a two-sentence reply with retries is 80-100 requests, 10-12 s and 600k-900k input
  tokens (about $0.03-0.04). The word heats dominate.
- The length question is unstable: the same message gets 7-10 words one run and 10-20 the next.
- The judge remains reliable ("It's Canberra." 0.97, "The town sydney." 0.03); nothing has reached
  90% except greetings on some runs.

## Ideas

- Combine what measured best: write left to right (so every choice sees a real sentence, the
  condition where flat lists score 95%), with heats and finals for the word and the whole-text
  comparison as the last step. About 4 waves per word.
- Use the judge as a goal *during* composition: keep a small beam of drafts and rank them by `responds`.
- Let the judge's `grammatical` score gate acceptance too; `responds` alone passes broken grammar.
- Add proper nouns to the dictionary (Canberra).
