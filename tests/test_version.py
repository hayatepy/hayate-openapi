from importlib.metadata import version

import hayate_openapi


def test_public_version_matches_distribution_metadata() -> None:
    assert hayate_openapi.__version__ == version("hayate-openapi")
