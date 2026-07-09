#!/usr/bin/env python3
"""Publish the refgenie data channel from this registry.

A "data channel" is what a refgenie1 client syncs recipes and asset classes
from: an ``index.yaml`` plus the recipe / asset-class YAML files, served over
HTTP (GitHub Pages). This registry is the single source of truth for those
files, so the channel is published in the registry's OWN native layout -- no
flattening, no format conversion. A channel path maps 1:1 to a registry path:

    asset_classes/<name>.yaml        ->  channel/asset_classes/<name>.yaml
    recipes/<name>/recipe.yaml       ->  channel/recipes/<name>/recipe.yaml

The generated ``index.yaml`` uses the ``dir`` + ``files`` structure the client
parses (``refgenie.managers.sources.manager.IndexFile``); recipe entries carry
the ``<name>/recipe.yaml`` subpath, which the client resolves to
``<base>/recipes/<name>/recipe.yaml`` on fetch.

A self-contained ``index.html`` landing page is also generated so the channel's
base URL (https://refgenie.github.io/refgenie-registry/) is browsable: it lists
the published asset classes and recipes and links to ``index.yaml`` and each
file. Machine clients ignore it and fetch ``index.yaml`` directly.

Only the channel artifact (``index.yaml``, ``index.html``, ``asset_classes/``,
``recipes/``) is written to the output dir -- nothing else from the repo is
exposed.

Usage:
    python tools/build_channel.py                 # -> ./channel/
    python tools/build_channel.py -o /tmp/channel
    python tools/build_channel.py --registry-root . -o channel
"""

import argparse
import shutil
import sys
from pathlib import Path

import yaml

ASSET_CLASSES_DIR = "asset_classes"
RECIPES_DIR = "recipes"
RECIPE_FILE = "recipe.yaml"


def collect_asset_classes(registry_root: Path) -> list[str]:
    """Return sorted ``<name>.yaml`` basenames under ``asset_classes/``."""
    src = registry_root / ASSET_CLASSES_DIR
    if not src.is_dir():
        return []
    return sorted(f.name for f in src.iterdir() if f.is_file() and f.suffix == ".yaml")


def collect_recipes(registry_root: Path) -> list[str]:
    """Return sorted ``<name>/recipe.yaml`` relative paths under ``recipes/``.

    Every recipe lives in its own directory as ``recipe.yaml``. A recipe
    directory missing ``recipe.yaml`` is an error (we do not silently skip it).
    """
    src = registry_root / RECIPES_DIR
    if not src.is_dir():
        return []
    entries: list[str] = []
    missing: list[str] = []
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        if (d / RECIPE_FILE).is_file():
            entries.append(f"{d.name}/{RECIPE_FILE}")
        else:
            missing.append(d.name)
    if missing:
        raise SystemExit(
            f"error: recipe directories missing {RECIPE_FILE}: {', '.join(missing)}"
        )
    return entries


CHANNEL_URL = "https://refgenie.github.io/refgenie-registry/index.yaml"

PAGE_CSS = """
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #1a1a2e; --muted: #5a5a72; --card: #f6f7fb;
  --border: #e2e4ee; --accent: #4a4ae0; --code-bg: #eceef6;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14141c; --fg: #e8e8f0; --muted: #9a9ab0; --card: #1e1e2a;
    --border: #2c2c3c; --accent: #8f8fff; --code-bg: #24242f;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.wrap { max-width: 860px; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }
h1 { font-size: 1.9rem; margin: 0 0 .25rem; letter-spacing: -0.02em; }
.tag { color: var(--muted); margin: 0 0 2rem; font-size: 1.05rem; }
h2 { font-size: 1.15rem; margin: 2.25rem 0 .75rem; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; padding: 1rem 1.25rem; margin: 1rem 0;
}
code, pre {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9em;
}
pre {
  background: var(--code-bg); border: 1px solid var(--border);
  border-radius: 8px; padding: .85rem 1rem; overflow-x: auto; margin: .5rem 0 0;
}
.counts { display: flex; gap: 1.5rem; flex-wrap: wrap; margin: 0; padding: 0; list-style: none; }
.counts li { color: var(--muted); }
.counts b { color: var(--fg); font-size: 1.5rem; display: block; }
.grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: .35rem .9rem; margin: .5rem 0 0; padding: 0; list-style: none;
}
.grid li { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.grid a { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .88rem; }
footer { margin-top: 3rem; color: var(--muted); font-size: .85rem; }
"""


def _name_list_html(entries: list[tuple[str, str]]) -> str:
    """Render (display_name, href) pairs as a <ul class=grid> of links."""
    items = "".join(
        f'<li><a href="{href}">{name}</a></li>' for name, href in entries
    )
    return f'<ul class="grid">{items}</ul>'


def render_landing_page(asset_files: list[str], recipe_files: list[str]) -> str:
    """Build a self-contained index.html listing the published channel contents."""
    asset_entries = [
        (f.removesuffix(".yaml"), f"{ASSET_CLASSES_DIR}/{f}") for f in asset_files
    ]
    recipe_entries = [
        (r.split("/")[0], f"{RECIPES_DIR}/{r}") for r in recipe_files
    ]
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>refgenie data channel</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>refgenie data channel</h1>
  <p class="tag">The canonical <a href="https://refgenie.org">refgenie</a> data
  channel, published from
  <a href="https://github.com/refgenie/refgenie-registry">refgenie-registry</a>.
  Recipes and asset classes a refgenie client syncs to build reference genome
  assets locally.</p>

  <div class="card">
    <ul class="counts">
      <li><b>{len(asset_entries)}</b> asset classes</li>
      <li><b>{len(recipe_entries)}</b> recipes</li>
    </ul>
  </div>

  <h2>Use it</h2>
  <div class="card">
    <p style="margin:0 0 .25rem">Point a refgenie client at this channel:</p>
    <pre>refgenie data-channel add -s {CHANNEL_URL}
refgenie data-channel sync</pre>
    <p style="margin:.75rem 0 0">Machine-readable index:
    <a href="index.yaml"><code>index.yaml</code></a></p>
  </div>

  <h2>Asset classes</h2>
  {_name_list_html(asset_entries)}

  <h2>Recipes</h2>
  {_name_list_html(recipe_entries)}

  <footer>
    Generated from the registry's native layout by
    <code>tools/build_channel.py</code>. To request a new recipe or asset class,
    open a PR or issue on
    <a href="https://github.com/refgenie/refgenie-registry">refgenie-registry</a>.
  </footer>
</div>
</body>
</html>
"""


def build_channel(registry_root: Path, out_dir: Path) -> dict:
    asset_files = collect_asset_classes(registry_root)
    recipe_files = collect_recipes(registry_root)

    # Fresh output dir; copy the two source trees verbatim (nested layout kept).
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    shutil.copytree(registry_root / ASSET_CLASSES_DIR, out_dir / ASSET_CLASSES_DIR)
    shutil.copytree(registry_root / RECIPES_DIR, out_dir / RECIPES_DIR)

    index = {
        "asset_class": {"dir": ASSET_CLASSES_DIR, "files": asset_files},
        "recipe": {"dir": RECIPES_DIR, "files": recipe_files},
    }
    with open(out_dir / "index.yaml", "w") as f:
        yaml.dump(index, f, default_flow_style=False, sort_keys=False)

    with open(out_dir / "index.html", "w") as f:
        f.write(render_landing_page(asset_files, recipe_files))
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry-root", default=".",
                        help="Registry root containing asset_classes/ and recipes/ (default: .)")
    parser.add_argument("-o", "--out-dir", default="channel",
                        help="Output channel directory (default: channel)")
    args = parser.parse_args()

    registry_root = Path(args.registry_root).resolve()
    out_dir = Path(args.out_dir).resolve()

    index = build_channel(registry_root, out_dir)
    print(f"Built channel at {out_dir}")
    print(f"  asset classes: {len(index['asset_class']['files'])}")
    print(f"  recipes:       {len(index['recipe']['files'])}")
    print(f"  index:         {out_dir / 'index.yaml'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
