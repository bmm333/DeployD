"""
Smoke test — ensures pytest can collect and the project structure is importable.
Delete this file once real domain tests exist (DD-25).
"""


def test_project_is_importable() -> None:
    """Verify the deployd package is installed and importable."""
    import deployd  # noqa: F401
