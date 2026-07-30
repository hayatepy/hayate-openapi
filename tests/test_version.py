import tomllib
from importlib.metadata import version
from pathlib import Path

import hayate_openapi

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_HOME = "https://hayatepy.dev/"
PUBLIC_PACKAGE_HOME = "https://hayatepy.dev/ecosystem/#hayate-openapi"
PUBLIC_COMPATIBILITY = "https://hayatepy.dev/evidence/compatibility/"
SUPERSEDED_DOCS_PREFIX = "https://github.com/hayatepy/.github/blob/main/docs/"


def test_public_version_matches_distribution_metadata() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert hayate_openapi.__version__ == project["version"] == version("hayate-openapi")


def test_public_discovery_uses_the_canonical_site() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert project["urls"]["Homepage"] == PUBLIC_PACKAGE_HOME
    assert f"[Start here]({PUBLIC_HOME})" in readme
    assert f"[Tested compatibility]({PUBLIC_COMPATIBILITY})" in readme
    assert SUPERSEDED_DOCS_PREFIX not in readme
