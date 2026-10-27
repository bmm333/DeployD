from demo.ui_text import split_answer


def test_a_short_answer_is_not_split() -> None:
    assert split_answer("Roll back the cache change.") == ("Roll back the cache change.", "")


def test_a_long_answer_is_cut_at_a_paragraph_break() -> None:
    first, second = "A" * 300, "B" * 300
    text = f"{first}\n\n{second}\n\n{'C' * 300}"

    head, rest = split_answer(text, limit=700)

    assert head == f"{first}\n\n{second}"
    assert rest == "C" * 300


def test_without_a_paragraph_break_it_cuts_between_list_items() -> None:
    items = "\n".join(f"- step {i}: " + "x" * 40 for i in range(30))

    head, rest = split_answer(items, limit=300)

    assert head.splitlines()[-1].startswith("- step")
    assert len(head) <= 300
    assert f"{head}\n{rest}" == items


def test_without_line_breaks_it_cuts_at_the_end_of_a_sentence() -> None:
    text = " ".join(f"Sentence number {i} says something." for i in range(40))

    head, rest = split_answer(text, limit=300)

    assert head.endswith(".")
    assert f"{head} {rest}" == text


def test_a_word_longer_than_the_limit_is_cut_hard_and_loses_nothing() -> None:
    text = "x" * 1000

    head, rest = split_answer(text, limit=300)

    assert (len(head), len(rest)) == (300, 700)
    assert head + rest == text
