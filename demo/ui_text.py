"""Text helpers for the live UI, kept free of Streamlit so they can be unit-tested."""

from __future__ import annotations

PREVIEW_CHARS = 650

# (marker, characters of the marker that stay in the preview): a paragraph break, a
# line break (a list item), the end of a sentence (keeps its full stop), then a space.
_CUTS = (("\n\n", 0), ("\n", 0), (". ", 1), (" ", 0))


def split_answer(text: str, limit: int = PREVIEW_CHARS) -> tuple[str, str]:
    """Split a long answer into a preview and the rest, cutting where markdown survives.

    Returns ``(text, "")`` when the answer is short enough.
    """
    if len(text) <= limit:
        return text, ""
    window = text[:limit]
    for marker, kept in _CUTS:
        cut = window.rfind(marker)
        if cut >= limit * 0.4:  # not so early that the preview would be a stub
            return text[: cut + kept].rstrip(), text[cut + len(marker) :].lstrip()
    return window.rstrip(), text[limit:].lstrip()
