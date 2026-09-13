#!/usr/bin/env python3
"""Check this repository's package contract and run its behavioral tests."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def relative_path(root: Path, value: str) -> Path:
    require(isinstance(value, str) and value.startswith("./"),
            f"Expected a ./ path: {value!r}")
    path = (root / value).resolve()
    require(path.is_relative_to(root.resolve()), f"Path escapes package: {value}")
    require(path.exists(), f"Missing package resource: {path}")
    return path


def check_package() -> Path:
    market = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]+", market["name"])),
            "Invalid marketplace name")
    require(bool(market["interface"]["displayName"].strip()), "Missing marketplace label")
    require(len(market["plugins"]) == 1, "Expected the ADHD plugin entry")
    entry = market["plugins"][0]
    require(entry["name"] == "adhd", "Unexpected plugin identifier")
    require(entry["source"]["source"] == "local", "Expected a repository-local plugin")
    require(entry["source"]["path"] == "./plugins/adhd", "Unexpected plugin path")
    require(entry["policy"]["installation"] == "AVAILABLE", "Expected available installation")
    require(entry["policy"]["authentication"] == "ON_INSTALL", "Unexpected authentication policy")
    require(entry["category"] == "Productivity", "Unexpected category")
    plugin = relative_path(ROOT, entry["source"]["path"])
    manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text())
    require(manifest["name"] == plugin.name, "Folder and manifest names disagree")
    require(bool(re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?",
                              manifest["version"])), "Invalid release version")
    require(bool(manifest["description"].strip()), "Missing description")
    require(bool(manifest["author"]["name"].strip()), "Missing author")
    require("[TODO:" not in json.dumps(manifest), "Unfinished manifest placeholder")
    require(not {"apps", "mcpServers", "hooks"}.intersection(manifest),
            "This skills-only package must not declare unconfigured integrations")
    interface = manifest["interface"]
    for key in ("displayName", "shortDescription", "longDescription", "developerName", "category"):
        require(bool(interface[key].strip()), f"Missing interface.{key}")
    prompts = interface["defaultPrompt"]
    require(isinstance(prompts, list) and 1 <= len(prompts) <= 3,
            "Expected one to three starter prompts")
    require(all(isinstance(p, str) and 0 < len(p) <= 128 for p in prompts),
            "Invalid starter prompt")
    for key in ("composerIcon", "logo"):
        require(relative_path(plugin, interface[key]).is_file(), f"Missing {key}")
    skill = relative_path(plugin, manifest["skills"]) / "adhd"
    content = (skill / "SKILL.md").read_text(encoding="utf-8")
    parts = content.split("---", 2)
    require(len(parts) == 3 and not parts[0].strip(), "Invalid skill frontmatter")
    metadata = yaml.safe_load(parts[1])
    require(metadata["name"] == skill.name, "Skill name and directory disagree")
    require(bool(metadata["description"].strip()), "Missing skill description")
    for link in re.findall(r"\]\(([^)]+)\)", parts[2]):
        if "://" not in link and not link.startswith("#"):
            relative_path(skill, "./" + link.split("#")[0])
    ui = yaml.safe_load((skill / "agents/openai.yaml").read_text())
    for key in ("icon_small", "icon_large"):
        relative_path(skill, "./" + ui["interface"][key])
    require(ui["policy"]["allow_implicit_invocation"] is True,
            "Preserve automatic skill discovery")
    require(set(ui["policy"]) <= {"allow_implicit_invocation"},
            "Unsupported skill policy fields in the plugin package")
    require(not list(plugin.rglob("state.yaml")), "Runtime state must not ship in the plugin")
    require((plugin / "requirements.txt").is_file(), "Missing runtime dependencies")
    return skill


def main() -> int:
    try:
        skill = check_package()
    except (ValueError, KeyError, TypeError, OSError, yaml.YAMLError) as error:
        print(f"Package check failed: {error}", file=sys.stderr)
        return 1
    print("Package contract: OK", flush=True)
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(skill / "tests"), "-v"],
        cwd=ROOT,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
