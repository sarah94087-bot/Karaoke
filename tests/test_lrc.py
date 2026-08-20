"""Reading LRC, the format every lyrics database answers in.

The cases here are the ones that produce a wrong *song* rather than an error:
a fraction read as an integer puts every line ten times too late, and a repeated
chorus dropped to one line loses half a song.
"""

from packages.lyrics.lrc import parse_lrc

SIMPLE = """[ti:עוף גוזל]
[ar:אריק איינשטיין]

[00:12.34]שורה ראשונה
[00:16.50]שורה שנייה
"""


def test_a_plain_file_becomes_timed_lines():
    lines = parse_lrc(SIMPLE)

    assert [line.text for line in lines] == ["שורה ראשונה", "שורה שנייה"]
    assert [line.start_ms for line in lines] == [12_340, 16_500]


def test_metadata_tags_are_not_lyrics():
    """`[ar:...]` looks exactly like a timestamp to a careless parser, and the
    user ends up singing the artist's name."""
    assert all("אריק" not in line.text for line in parse_lrc(SIMPLE))


def test_hundredths_and_thousandths_are_both_read_as_fractions():
    """`.5` is half a second, not five milliseconds. Getting this wrong shifts
    every line in the file."""
    lines = parse_lrc("[00:01.5]א\n[00:02.05]ב\n[00:03.005]ג\n")

    assert [line.start_ms for line in lines] == [1_500, 2_050, 3_005]


def test_minutes_beyond_sixty_still_parse():
    assert parse_lrc("[04:03.00]שורה")[0].start_ms == 243_000


def test_a_colon_between_seconds_and_hundredths_is_accepted():
    """Some files use `[00:12:34]`, and it means the same thing."""
    assert parse_lrc("[00:12:34]שורה")[0].start_ms == 12_340


def test_a_line_sung_twice_becomes_two_lines():
    """LRC writes a repeated chorus as one line with several timestamps. Keeping
    only the first loses the second half of a song."""
    lines = parse_lrc("[00:16.00][02:04.00]פזמון\n")

    assert [line.start_ms for line in lines] == [16_000, 124_000]
    assert {line.text for line in lines} == {"פזמון"}


def test_lines_come_back_in_time_order():
    lines = parse_lrc("[00:30.00]שנייה\n[00:10.00]ראשונה\n")

    assert [line.text for line in lines] == ["ראשונה", "שנייה"]


def test_an_empty_timestamp_is_a_musical_gap_not_a_line():
    assert [line.text for line in parse_lrc("[00:10.00]\n[00:20.00]שורה\n")] == ["שורה"]


def test_untimed_lines_are_dropped():
    """A file with no timestamps is plain lyrics, and the caller asked for
    synchronised ones."""
    assert parse_lrc("שורה בלי זמן\nעוד שורה\n") == []


def test_word_tags_become_word_timings():
    lines = parse_lrc("[00:20.10]<00:20.10>מילה <00:20.90>אחרי\n")

    assert lines[0].text == "מילה אחרי"
    assert [word["start_ms"] for word in lines[0].words] == [20_100, 20_900]


def test_a_partly_tagged_line_keeps_no_word_timings():
    """A highlight that stops halfway through a line looks broken; the same rule
    the store applies when it decides between `word` and `line`."""
    lines = parse_lrc("[00:20.10]מילה <00:20.90>אחרי\n")

    assert lines[0].words == []
    assert lines[0].start_ms == 20_100


def test_word_timings_shift_with_a_repeated_line():
    """The second time the chorus is sung, its words are sung then too."""
    lines = parse_lrc("[00:10.00][01:10.00]<00:10.00>מילה <00:11.00>שנייה\n")

    assert [word["start_ms"] for word in lines[0].words] == [10_000, 11_000]
    assert [word["start_ms"] for word in lines[1].words] == [70_000, 71_000]


def test_nothing_at_all_is_no_lines_rather_than_an_error():
    assert parse_lrc("") == []
