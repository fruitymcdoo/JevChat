# JevChat

A chat interface on top of [Jev](https://docs.typesafe.ai), TypeSafe's decision model.
Jev never writes text, and there is no canned text either: every word of a reply is the result of
typed decisions over a 17,000-word dictionary, and a judge (also Jev) decides whether the reply is
good enough to send. The UI streams words as they are decided and shows every decision underneath.

```
user> I just got a new puppy and she is adorable
 bot> Wow she's cute! What name is she?          (accepted at 94%)
user> her name is Biscuit
 bot> Wow Biscuit! Adorable!                      (best effort, 87%)
```

## Run

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
# put TYPESAFE_API_KEY=... in .env
.venv\Scripts\python app.py           # http://localhost:5000
.venv\Scripts\python smoke_test.py -v # scripted conversation with each word's decisions (--parallel for the other composer)
```

Without an API key the app falls back to a keyword-overlap `MockDecider` so the wiring still runs.

## How a reply is built

There are two composers, selectable in the UI. **Left to right** is the default and much the better one.

### Left to right (`jevchat/linear.py`)

Every measurement below points the same way: Jev is very good at picking a whole word when it can
see a real sentence, and weak when the choice is removed from that. So the reply is written one word
at a time, and every choice sees the real text so far.

| Stage | What happens |
| --- | --- |
| plan | 1 request: what the reply should do, its tone, "How many words should an ideal response to this query contain?" (1-3 ... 30+), which sets the word limit, and whether the reply suits an emoji (yes for good news and casual chat, no for bereavement or facts). |
| kind | What kind of word comes next: pronoun, helper verb, noun, verb, ..., a number, an emoji (if the plan said yes; at most 2, never adjacent, and free of the word limit), a word echoed from the user, punctuation, or stop. The top 3 kinds all go on. |
| heats | A big kind is several flat lists of 255 words by frequency (nouns: 20 lists). Every list is asked at once and sends its best 3 words to a final. |
| finals | One flat choice per kind among the heat winners. Its top 3 are nominated; with the thesaurus on, so are synonyms of its favourite. |
| compare | The nominees rendered as whole texts ("Sorry about your", "Sorry about that", ...). Jev picks the one that reads best, or stops, or goes back (only at 80%+ probability). |
| go back | Jev also chooses *how far*: the last 1, 2, 3, 4, 6 or 8 words, the whole last sentence, or everything, each shown as the text it would leave. Nothing is banned afterwards. Jev is told what was tried from that point and taken back, and writes on freely. |
| assess | 3 yes/no questions about the finished reply: `responds`, `grammatical`, `complete`. Accepted when `responds` reaches `ACCEPT_THRESHOLD` (default 0.80, adjustable per message in the UI) and `grammatical` clears a floor of 0.25 (truly broken text scores under 0.1). |
| retry | On rejection Jev chooses how far to go back, exactly as above, and writing resumes, up to 3 attempts. Then the best attempt is sent, flagged as below the bar. |

There are no word bans. An earlier version forbade repeating a content word and ruled out any word that had
been undone; that stopped a recipe from saying "coconut" or "pineapple" when it needed them. The only rule left
is no immediate stammer ("the the"). Emoji may only follow a closing mark, at most two per reply.

Every composing prompt opens with the task context (who is speaking, what a reply is for), and every
word question carries a hint not to reuse the user's words; see the parrot experiment below.

Typical cost: about 4 waves and 35k-45k input tokens per word with the 17,000-word dictionary (a noun search alone is
40 lists). A one-word answer is 1-2 s and about 90k tokens; a 20-word reply is 15-25 s and 1M-1.5M tokens (about
$0.04-0.06). Word searches are remembered within a reply, so going back and rewriting the same text is not paid for twice.

### Parallel slots (`jevchat/composer.py`)


The reply is a row of numbered **slots**, each holding one word, one punctuation mark, or nothing.
Every open slot is asked about at the same time, so a round costs the same few request-waves
however long the reply is. (A wave is a set of independent questions over one state, split across
concurrent requests when it would not fit in one.)

| Stage | What happens |
| --- | --- |
| plan | 1 request: what the reply should do, its tone, and "How many words should an ideal response to this query contain?" (1-3, 4-6, 7-10, 10-20, 20-30, 30+). The answer sets the layout (`LAYOUT`): how many sentences, and how many slots in each. Sentences are written one at a time, each seeing the finished ones, because parallel filling is sharp up to about nine slots. |
| fill | Rounds of 3 waves. **Kind:** every open slot is asked what kind of word it needs. **Heats:** the few slots Jev is surest about (`FOCUS_SLOTS`) search for their word: a big kind such as nouns is 20 flat lists of 255 words by frequency, all asked at once, each sending its best 3 to a final. **Finals:** one flat choice among the heat winners gives P(word given kind); times P(kind), these are the slot's potentials. With the thesaurus on, synonyms of each branch's favourite join them. Only the surest slots are locked each round (`COMMIT_MIN_P`, never two neighbours at once); the rest are asked again with those anchors in view. |
| settle | 2 waves: every slot compares whole-text versions of the reply, one per candidate (odd slots, then even). The judge referees: the settled draft is kept only if it scores higher. |
| assess | 3 yes/no questions about the draft: `responds` (the gate), `grammatical`, `complete`. Accepted at `ACCEPT_THRESHOLD` (default 0.80, adjustable per message in the UI). |
| reopen | On rejection the shakiest third of slots is blanked and filled again, up to `MAX_ATTEMPTS` drafts and `MAX_ROUNDS` rounds. Then the best-scoring draft is sent, flagged as below the bar. |

Why lock gradually: asked blind, slots in the middle of a sentence all give the same blurry answer
("feel, feel, feel") because each imagines a different sentence, while the edges are sharp ("sorry"
first, "." last). Locking only confident slots lets a sentence grow from its anchors:
`where ___ ___ ___ ___ ?` -> `where do you ___ ___ ?` -> `Where do you go hiking?`

## Layout

- `app.py` — Flask server; `/api/chat` streams NDJSON events (`plan`, `token`, `back`, `end`, `assess`, `done`; the parallel composer adds `draft`, `settle`, `reopen`)
- `jevchat/decider.py` — the only code that talks to Jev; normalises answers into a trace
- `jevchat/linear.py` — the left-to-right composer (default); tuning constants at the top
- `jevchat/composer.py` — the parallel slot-filling composer, plus the plan, judge and prompts both share
- `jevchat/lexicon.json` — dictionary (~17,300 entries as flat lists per kind of word: frequent words including 1,400 names that render capitalised, 148 numbers as digits and words, 119 emoji each with a short description for Jev) and thesaurus (~9,000 entries)
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

### Is the judge too harsh? (`experiments/judge_calibration.py`)

26 hand-labelled replies: 7 good, 9 fair ("I'd pick Valve because they are awesome"), 10 bad (wrong, evasive,
parroted, broken).

| Judge question | Fair and good replies passing at 80% | Bad replies leaking at 80% |
| --- | --- | --- |
| "Is this a good reply...?" (before) | 11 of 16 | 0 of 10 |
| "Is this an acceptable reply...? It does not have to be detailed, complete or perfectly worded..." (now) | 16 of 16 | 0 of 10 |
| "Imagine a friendly person with a small vocabulary said this..." | 13 of 16 | 0 of 10 |

Wrong answers stay near zero under the new wording ("The city is Sydney." 0.02, "Seven." for 2 plus 2, 0.02).

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

Left to right, with the 17,000-word dictionary, no bans, and the calibrated judge (80% bar):

| Message | Reply | `responds` |
| --- | --- | --- |
| what's the capital of Australia? | Canberra. | 96%, accepted |
| I had a rough day at work today | Sorry you had a rough day 🤗 I'm sympathetic. How can I help? ❤️ | 97%, accepted |
| How can I make a pina colada? | I can help explain about a pina colada. Make a pina colada by blend the pineapple juice with some coconut cream. 😊 | 91%, accepted |
| why is the sky blue? | It's scattered sunlight. | 87%, accepted |
| Which game studio makes the best RPGs? | I think I'm unsure. But it depends on your taste. What genres of games? | 80%, accepted |
| I got the job!! | Congratulations! 🎉 | 98%, accepted |

- Multi-sentence replies are coherent for the first time, and rewinding now usually improves the reply
  (the puppy reply went from "How do you name her?" at 85% to "What breed?" at 91%).
- Emoji land well once the plan decides whether one fits: "Wow she's lovely 🥰 how is she?" (93%, accepted first
  try), "Congratulations on the job 🥳 you're amazing!" (91%), "Bye 💤 have yourself wonderful dreams!" (91%);
  none for "my grandfather passed away last week".
- Numbers work as words or digits ("what is 2 plus 2?" gives "Four.", 96%; days in a week gives "Seven.", then "7.").
  But "How many of the letter R are in the word Strawberry?" still gives "Two.", and the judge accepts it at 90%:
  Jev believes it. That is not a vocabulary gap. Jev does not see words as letters (0 of 27 in the spelling
  experiment) and counting is a documented weakness; TypeSafe's own advice is to keep arithmetic in code.
- Short answers to direct questions score low with the judge ("I do!" 48%); the 1-3 word plan leaves
  no room to say more.
- Knowledge was a vocabulary problem more than a Jev problem: with Canberra in the dictionary it answers
  "Canberra." at once, and with coconut and pineapple it writes a recipe. Its facts are still only as good as Jev's
  (it once preferred Disney as a game studio).
- Going back used to look "touchy" for a structural reason (`experiments/undo_probabilities.py`): after a
  sentence ended, the comparison sometimes offered only "stop" or "go back", and Jev chose "go back" at 83-89%
  simply because it wasn't finished talking. Stopping now always competes with a real continuation, and going
  back needs 80%. The RPG question went from 7 go-backs and 449 requests to none and 97.
- Going back without bans can repeat itself: Jev sometimes rewrites the same words it just took back ("I think I'm"
  twice). The per-attempt limit on going back keeps this bounded.
- The parallel composer manages single short sentences ("Where do you hike to?" 85%) but not longer
  ones ("Sorry, are you feel rough?. What do did you thing do happened?" 36%), at several times the cost.

## Ideas

- Use the judge as a goal *during* composition: keep a small beam of drafts and rank them by `responds`.
- Cut cost: skip the heats when one kind is near-certain to be a small closed class; cache heats across rewinds.
- Let the plan ask for more room when the user asks a direct question.
- Add proper nouns to the dictionary (Canberra).
