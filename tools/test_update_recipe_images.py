"""Tests for picking and writing recipe container pins (tools/update_recipe_images.py).

Run: pytest tools/test_update_recipe_images.py
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import update_recipe_images as uri  # noqa: E402


@pytest.mark.parametrize(
    "current, tags, expected",
    [
        ("0.7.9--h1_0", ["0.7.19--h2_0", "0.7.10--h3_5"], "0.7.19--h2_0"),
        ("2.5.5--ha_0", ["2.5.5--hb_2", "2.5.5--hc_1"], "2.5.5--hb_2"),
        ("2.7.11a--ha_8", ["2.7.11b--hb_0", "2.7.11--hc_9"], "2.7.11b--hb_0"),
        ("1.6.6--py310ha_1", ["1.6.6--py311hb_1", "1.6.6--py39hc_1"], "1.6.6--py311hb_1"),
        ("1.24--ha_0", ["1.23--hb_9", "latest"], "1.24--ha_0"),
        ("1.24--ha_0", ["1.25rc1--hb_0"], "1.24--ha_0"),
    ],
    ids=["numeric", "build", "letter", "python", "never-down", "prerelease"],
)
def test_newest_tag(current, tags, expected):
    assert uri.newest(current, tags) == expected


def test_rewrite_moves_the_pin_and_bumps_the_recipe_version(tmp_path):
    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(
        "name: bwa_index\n"
        "version: 0.2.1\n"
        "docker_image: quay.io/biocontainers/bwa:0.7.19--h1_1\n"
        "custom_seek_keys:\n"
        "  version: bwa 2>&1  # keep me\n"
    )
    new_version = uri.rewrite(
        recipe,
        "quay.io/biocontainers/bwa:0.7.19--h1_1",
        "quay.io/biocontainers/bwa:0.7.20--h2_0",
        "0.2.1",
    )
    assert new_version == "0.2.2"
    assert recipe.read_text() == (
        "name: bwa_index\n"
        "version: 0.2.2\n"
        "docker_image: quay.io/biocontainers/bwa:0.7.20--h2_0\n"
        "custom_seek_keys:\n"
        "  version: bwa 2>&1  # keep me\n"
    )
