"""Builds jevchat/lexicon.json: the decision tree of words plus a thesaurus.

Dev-time only (needs `pip install nltk wordfreq` and the WordNet corpus).
The app reads the JSON and has no NLP dependencies at runtime.

    dictionary  the most frequent English surface forms (wordfreq), sorted by
                kind of word. Each kind is one or more flat lists of at most 255
                words in frequency order. There is deliberately no finer
                grouping: measured on known answers, Jev picks the right word
                from a flat list of 255 about 95% of the time, but only found
                the right WordNet meaning area 50% of the time.
    thesaurus   WordNet synonyms for each open-class word
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

from nltk.corpus import wordnet as wn
from wordfreq import top_n_list

TOP_N = 14000
MAX_LEAF = 255  # Jev's limit on Choice options
MAX_SYNONYMS = 12
MAX_LISTS = {"noun": 20, "verb": 12, "adjective": 8, "adverb": 3}  # x255 words; every list is a question per slot, so this is the cost dial

# Closed-class words: WordNet doesn't cover them, and they're a small fixed set.
CLOSED = {
    "pronoun": (
        "A pronoun, which stands in for a person or thing, like I, you, it, they, this or something.",
        "i you he she it we they me him her us them my your his its our their mine yours myself yourself "
        "this that these those someone something anyone anything everyone everything nobody nothing one who "
        "there here".split(),
    ),
    "question_word": (
        "A question word that opens a question, like what, why or how.",
        "what why how when where who which whose".split(),
    ),
    "helper_verb": (
        "A helper verb: a form of be, have or do, or a word like can, will or should. This includes short forms like I'm, don't and it's.",
        "am is are was were be been being have has had do does did will would can could should may might must "
        "i'm you're it's that's he's she's we're they're i've you've we've i'll you'll it'll i'd you'd "
        "don't doesn't didn't can't won't isn't aren't wasn't couldn't wouldn't shouldn't haven't let's there's what's".split(),
    ),
    "determiner": (
        "A word that goes before a noun to point at it or count it, like the, a, some, any, every, many or more.",
        "the a an some any no every each all both many much more most few little less several another other such "
        "enough one first second last next".split(),
    ),
    "preposition": (
        "A preposition, which links to a noun, like of, in, on, at, with, about, for or to.",
        "of in on at to for with about from by into over under after before between through during without "
        "against like as near around across behind beyond up down out off than per".split(),
    ),
    "conjunction": (
        "A joining word that connects two parts of a sentence, like and, but, or, because, if, so or that.",
        "and but or so because if when while although though unless until since that whether than then".split(),
    ),
    "social_word": (
        "A social or reaction word, like hello, hi, yes, no, thanks, sorry, please, okay, wow or goodbye.",
        "hello hi hey yes no yeah nope thanks thank please sorry okay ok sure wow oh ah well hmm goodbye bye "
        "welcome congratulations cheers haha not never".split(),
    ),
}

NUMBERS = (
    [str(n) for n in range(0, 101)] + ["200", "300", "400", "500", "1000", "2000", "10000", "100000", "1000000"]
    + "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
      "eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion "
      "half dozen couple third fourth fifth".split()
)

# Emoji carry a short description, because the symbol alone tells Jev little.
EMOJI = {
    "😀": "grinning face, happy", "😄": "big smile, delighted", "😊": "warm smile, pleased", "🙂": "slight smile, friendly",
    "😉": "wink, playful", "😂": "tears of joy, laughing hard", "🤣": "rolling on the floor laughing", "😅": "nervous laugh, relief",
    "😍": "heart eyes, adoring", "🥰": "smiling with hearts, affection", "😘": "blowing a kiss", "😎": "sunglasses, cool",
    "🤔": "thinking, not sure", "🤨": "raised eyebrow, doubtful", "😐": "neutral face", "🙄": "rolling eyes, annoyed",
    "😏": "smirk", "😴": "sleeping, tired", "🥱": "yawning, bored or sleepy", "😢": "crying, sad", "😭": "sobbing, very sad",
    "😞": "disappointed", "😔": "pensive, down", "😟": "worried", "😬": "grimace, awkward", "😳": "flushed, embarrassed",
    "😱": "screaming, shocked", "😮": "open mouth, surprised", "🤯": "mind blown, amazed", "😡": "angry", "😤": "huffing, frustrated",
    "🤗": "hugging, comforting", "🤩": "star struck, excited", "🥳": "party face, celebrating", "😇": "angel, innocent",
    "🤒": "sick with a thermometer", "🤕": "hurt, bandaged head", "🥺": "pleading eyes", "😋": "yum, tasty", "🤤": "drooling, craving",
    "👍": "thumbs up, agree, good", "👎": "thumbs down, disagree, bad", "👏": "clapping, well done", "🙌": "raised hands, hooray",
    "🙏": "folded hands, thanks or please", "💪": "flexed arm, strong, you can do it", "👋": "waving hand, hello or goodbye",
    "🤝": "handshake, deal", "🤞": "fingers crossed, good luck", "👀": "eyes, looking, curious", "🫶": "heart hands, love and support",
    "❤️": "red heart, love", "💔": "broken heart", "💕": "two hearts, affection", "✨": "sparkles, wonderful", "🔥": "fire, awesome",
    "⭐": "star, excellent", "🎉": "party popper, congratulations", "🎊": "confetti, celebration", "🎂": "birthday cake", "🎁": "gift",
    "🏆": "trophy, winner", "💯": "one hundred, perfect", "✅": "check mark, yes, done", "❌": "cross mark, no, wrong",
    "❓": "question mark", "❗": "exclamation mark, important", "💡": "light bulb, idea", "💤": "sleep", "💰": "money",
    "☀️": "sun, sunny", "🌧️": "rain", "⛈️": "storm", "❄️": "snow, cold", "🌈": "rainbow", "🌙": "moon, night", "🌊": "wave, sea",
    "🌲": "tree, forest", "🌸": "blossom, flower", "🌹": "rose", "🍀": "four leaf clover, luck", "⛰️": "mountain", "🏖️": "beach",
    "🐶": "dog, puppy", "🐱": "cat, kitten", "🐦": "bird", "🐟": "fish", "🐴": "horse", "🦋": "butterfly", "🐾": "paw prints, pet",
    "🍕": "pizza", "🍔": "burger", "🍰": "cake, dessert", "🍎": "apple", "🍓": "strawberry", "☕": "coffee", "🍵": "tea",
    "🍺": "beer", "🍷": "wine", "🍳": "cooking, frying pan", "🥗": "salad, healthy food",
    "🎵": "music note", "🎸": "guitar", "🎮": "video game", "📚": "books, reading, study", "✏️": "pencil, writing", "🎬": "film, movie",
    "⚽": "football, soccer", "🏀": "basketball", "🏃": "running", "🥾": "hiking boot", "🚗": "car, driving", "✈️": "plane, travel",
    "🏠": "house, home", "💼": "briefcase, work", "💻": "laptop, computer", "📱": "phone", "⏰": "alarm clock, time", "📅": "calendar, date",
}

POS_NAMES = {"n": "noun", "v": "verb", "a": "adjective", "r": "adverb"}


def pos_profile(word: str) -> dict[str, float]:
    """Share of the word's usage per part of speech, by SemCor sense counts."""
    counts = Counter()
    synsets = Counter()
    for pos in "nvar":
        lemma = wn.morphy(word, pos)
        if not lemma:
            continue
        for s in wn.synsets(lemma, pos):
            synsets[pos] += 1
            for l in s.lemmas():
                if l.name().lower() == lemma:
                    counts[pos] += l.count()
    basis = counts if sum(counts.values()) >= 3 else synsets
    total = sum(basis.values())
    profile = {p: c / total for p, c in basis.items()} if total else {}
    for pos in "ar":  # "excited" is rare as an adjective in SemCor next to "excite", but it is one
        if synsets[pos] and wn.morphy(word, pos) == word and word.endswith(("ed", "ing", "ly")):
            profile[pos] = max(profile.get(pos, 0.0), 0.25)
    return profile


def synonyms(word: str, pos: str) -> list[str]:
    lemma = wn.morphy(word, pos)
    if lemma != word:  # inflected form: a lemma synonym wouldn't fit the sentence
        return []
    out: dict[str, None] = {}
    for s in wn.synsets(lemma, pos)[:3]:  # most common senses only
        for l in s.lemmas():
            name = l.name().lower()
            if name != word and name.isalpha():
                out.setdefault(name)
    return list(out)[:MAX_SYNONYMS]


def main():
    closed_words = {w for _, words in CLOSED.values() for w in words} | set(NUMBERS)
    words_of: dict[str, list[str]] = defaultdict(list)
    thesaurus: dict[str, list[str]] = {}

    for word in top_n_list("en", TOP_N):  # already in frequency order
        if word in closed_words or not word.isalpha() or len(word) < 2:
            continue
        for pos, share in pos_profile(word).items():
            if share < 0.25:
                continue
            kind = POS_NAMES["a" if pos == "s" else pos]
            if len(words_of[kind]) < MAX_LISTS[kind] * MAX_LEAF and word not in words_of[kind]:
                words_of[kind].append(word)
                syn = synonyms(word, pos)
                if syn:
                    thesaurus.setdefault(word, syn)

    def node(key, desc, words, glosses=None):
        n = {"key": key, "desc": desc, "lists": [words[i:i + MAX_LEAF] for i in range(0, len(words), MAX_LEAF)]}
        if glosses:
            n["glosses"] = glosses  # word -> short description shown to Jev alongside it
        return n

    tree = [node(k, desc, words) for k, (desc, words) in CLOSED.items()]
    tree.append(node("number", "A number, as a digit (3, 12, 100) or a number word (three, twelve, hundred).", NUMBERS))
    tree.append(node("emoji", "An emoji, to show a feeling or decorate the reply, usually at the end of a sentence.",
                     list(EMOJI), glosses=EMOJI))
    tree.append(node("noun", "A noun: a person, thing, place, idea or time.", words_of["noun"]))
    tree.append(node("verb", "A main verb: an action, an event or a state, in any tense or form.", words_of["verb"]))
    tree.append(node("adjective", "An adjective, which describes what something is like, such as good, big, happy, new or hard.", words_of["adjective"]))
    tree.append(node("adverb", "An adverb, which says how, how much, when or where, like very, really, now, too, always or just.", words_of["adverb"]))

    out = Path(__file__).resolve().parents[1] / "jevchat" / "lexicon.json"
    out.write_text(json.dumps({"tree": tree, "thesaurus": thesaurus}, indent=1), encoding="utf-8")

    for n in tree:
        print(f"{n['key']:14} {sum(map(len, n['lists'])):5} words in {len(n['lists'])} list(s)")
    everything = {w for n in tree for l in n["lists"] for w in l}
    print(f"total {len(everything)} distinct words, thesaurus entries: {len(thesaurus)}  ->  {out}")
    probe = "exhausting dish miserable pets make try view excited news".split()
    print("coverage probe:", {w: [n["key"] for n in tree if any(w in l for l in n["lists"])] for w in probe})


if __name__ == "__main__":
    main()
