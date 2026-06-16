"""Shared pytest configuration for the voice test suite.

Additive test-hygiene infra (WorldOS QA Lab Phase-1). Registers the custom
markers used to opt slow / occasionally-flaky tests out of the default fast
lane. Empty == today's behavior: nothing is marked yet, so collection and
execution are unchanged. This file only *declares* the markers so that
`-W error::pytest.PytestUnknownMarkWarning` (or `--strict-markers`) stays
green once tests start using them.
"""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    )
    config.addinivalue_line(
        "markers",
        "flaky: marks tests as occasionally flaky (may be retried or quarantined)",
    )
