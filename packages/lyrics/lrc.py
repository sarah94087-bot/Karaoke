"""Reading LRC, the format every synchronised-lyrics database speaks.

    [00:12.34]שורה ראשונה
    [00:16.00][02:04.00]פזמון שחוזר פעמיים
    [00:20.10]<00:20.10>מילה <00:20.90>אחרי מילה

Three things this deliberately does not do:

* **No `end_ms`.** LRC has no ends, only starts. Deriving one from the next
  line's start would make a guess indistinguishable from a measurement, and the
  player already knows how to show a line whose end is unknown (T-2.1).
* **`[offset:…]` is parsed and ignored.** The tag is rare, and the sign
  convention for it is not agreed between players - half of them shift one way.
  Applying it on a guess would move every line of the song. The user's own
  offset control (T-2.7) is the honest place to fix a file that sits early.
* **No writing.** Nothing needs to emit LRC yet, and a formatter with no caller
  is a formatter nobody notices is wrong.
"""

import re

from packages.core.lyrics import LineDraft

# [mm:ss.xx] or [mm:ss:xx] or [mm:ss] - minutes are not capped at two digits,
# because a long recording is a real thing and `[123:45.00]` is well formed.
TIMESTAMP = re.compile(r"\[(\d{1,4}):([0-5]?\d(?:[.:]\d{1,3})?)\]")
# [ar:...], [ti:...], [offset:+500] - a tag whose first character is a letter.
METADATA = re.compile(r"^\[([a-zA-Z#]+):(.*)\]$")
# <mm:ss.xx> before a word, in "enhanced" LRC.
WORD_TAG = re.compile(r"<(\d{1,4}):([0-5]?\d(?:[.:]\d{1,3})?)>")


def _to_ms(minutes: str, seconds: str) -> int:
    """`12`, `34.56` -> 754560ms. Hundredths and thousandths both occur."""
    whole, _, fraction = seconds.replace(":", ".").partition(".")
    # ".5" is five hundred milliseconds, ".05" is fifty: pad rather than parse
    # as an integer, or every two-digit file is out by a factor of ten.
    millis = int((fraction or "0").ljust(3, "0")[:3])
    return (int(minutes) * 60 + int(whole)) * 1000 + millis


def _words(text: str) -> tuple[str, list[dict]]:
    """Split enhanced-LRC word tags out of a line.

    Returns the plain text and the word timings. A line where only some words
    are tagged yields no words at all - a highlight that stops halfway through a
    line looks broken - which is the same rule the store applies (T-2.1).
    """
    matches = list(WORD_TAG.finditer(text))
    if not matches:
        return text.strip(), []

    words: list[dict] = []
    leading = text[: matches[0].start()].strip()
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        word = text[match.end() : end].strip()
        if word:
            words.append({"text": word, "start_ms": _to_ms(match.group(1), match.group(2))})

    plain = " ".join(part for part in [leading, *[word["text"] for word in words]] if part)
    # Untagged text before the first tag means the line is only partly aligned.
    return plain, [] if leading else words


def parse_lrc(text: str) -> list[LineDraft]:
    """Every timed line in the file, in time order.

    Untimed lines are dropped rather than kept untimed: a file with no
    timestamps at all is plain lyrics, and the caller asked for synchronised
    ones. `plainLyrics` from a catalogue is a different field and a different
    decision (T-2.10 is where a user pastes words with no timing).
    """
    drafts: list[LineDraft] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or METADATA.match(line):
            continue

        stamps = []
        while match := TIMESTAMP.match(line):
            stamps.append(_to_ms(match.group(1), match.group(2)))
            line = line[match.end() :]
        if not stamps:
            continue

        content, words = _words(line)
        if not content:
            # A timestamp with nothing on it is how LRC writes a musical gap.
            continue

        # A line can carry several timestamps when it is sung more than once;
        # each is a separate line in the song.
        for start in stamps:
            offset = start - stamps[0]
            drafts.append(
                LineDraft(
                    text=content,
                    start_ms=start,
                    words=[dict(word, start_ms=word["start_ms"] + offset) for word in words],
                )
            )

    drafts.sort(key=lambda draft: draft.start_ms or 0)
    return drafts
