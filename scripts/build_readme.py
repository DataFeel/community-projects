"""Rebuild the project list in README.md.

Reads curated projects from projects.yml, checks each repo on GitHub,
discovers other repos tagged with the configured topic, and rewrites the
section of README.md between the PROJECTS markers.

Usage:
    GITHUB_TOKEN=... python scripts/build_readme.py

GITHUB_TOKEN is optional locally but strongly recommended: without it the
GitHub API allows only 60 requests per hour.
"""

import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "projects.yml"
README_FILE = ROOT / "README.md"
START = "<!-- PROJECTS:START -->"
END = "<!-- PROJECTS:END -->"
API = "https://api.github.com"


def gh_get(path):
    """GET a GitHub API path. Returns parsed JSON, or None on 404."""
    req = urllib.request.Request(API + path)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "datafeel-community-projects")
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        if err.code in (404, 451):
            return None
        if err.code == 403:
            sys.exit("GitHub API rate limit or permission error. Set GITHUB_TOKEN and retry.")
        raise


def load_data():
    with DATA_FILE.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    settings = data.get("settings") or {}
    projects = data.get("projects") or []
    for i, p in enumerate(projects, start=1):
        for field in ("name", "repo", "event", "description"):
            if not p.get(field):
                sys.exit(f"projects.yml entry {i} is missing required field '{field}'.")
        if p["repo"].count("/") != 1:
            sys.exit(f"projects.yml entry {i}: repo must look like 'owner/name', got '{p['repo']}'.")
    return settings, projects


def fmt_date(iso):
    # Non-breaking hyphens keep the date on one line in narrow table columns.
    return iso[:10].replace("-", "\u2011") if iso else ""


CODE_BADGE = "https://img.shields.io/badge/Code-24292f?style=flat&logo=github&logoColor=white"
DEMO_BADGE = "https://img.shields.io/badge/Demo-2E97A8?style=flat"
ARCHIVED_BADGE = "https://img.shields.io/badge/Archived-8a8a8a?style=flat"


def cell(text):
    """Make text safe for a single Markdown table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def anchor(heading):
    """GitHub's anchor for a heading: lowercase, punctuation dropped, spaces to hyphens."""
    slug = re.sub(r"[^\w\- ]", "", heading.lower())
    return "#" + slug.replace(" ", "-")


def render_member(m):
    name = cell(m.get("name", ""))
    if m.get("github"):
        name = f"[{name}](https://github.com/{m['github']})"
    if m.get("linkedin"):
        name += f" <sub>[LinkedIn]({m['linkedin']})</sub>"
    return name


def render_curated(projects):
    if not projects:
        return "_No curated projects yet._\n"

    events = []
    for p in projects:
        if p["event"] not in events:
            events.append(p["event"])

    lines = []
    if len(events) > 1:
        jumps = " · ".join(f"[{e}]({anchor(e)})" for e in events)
        lines.append(f"**Jump to:** {jumps}\n")

    for event in events:
        lines.append(f"### {event}\n")
        lines.append("| Project | What it does | Team | Links | Updated |")
        lines.append("| --- | --- | --- | :---: | --- |")
        for p in (p for p in projects if p["event"] == event):
            meta = gh_get(f"/repos/{p['repo']}")
            code_url = f"https://github.com/{p['repo']}"

            name = cell(p["name"])
            project = f"**[{name}]({code_url})**" if meta else f"**{name}**"
            tags = []
            if p.get("featured"):
                tags.append("★ Featured")
            if p.get("track"):
                tags.append(f"{cell(p['track'])} track")
            if tags:
                project += "<br><sub>" + " · ".join(tags) + "</sub>"

            team = "<br>".join(render_member(m) for m in p.get("team") or [])

            links = []
            if meta is None:
                links.append("<sub>Repo unavailable</sub>")
            else:
                links.append(f'<a href="{code_url}"><img src="{CODE_BADGE}" alt="Code"></a>')
                if meta.get("archived"):
                    links.append(f'<img src="{ARCHIVED_BADGE}" alt="Archived">')
            if p.get("demo"):
                links.append(f'<a href="{p["demo"]}"><img src="{DEMO_BADGE}" alt="Demo"></a>')

            updated = fmt_date(meta.get("pushed_at")) if meta else ""
            lines.append(
                f"| {project} | {cell(p['description'])} | {team} | "
                f"{'<br>'.join(links)} | {updated} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def discover(settings, curated_repos):
    topic = settings.get("topic")
    if not topic:
        return []
    excluded_owners = {o.lower() for o in settings.get("exclude_owners") or []}
    excluded_repos = {r.lower() for r in settings.get("exclude_repos") or []}

    results = []
    page = 1
    while True:
        q = urllib.parse.quote(f"topic:{topic}")
        data = gh_get(f"/search/repositories?q={q}&sort=updated&per_page=100&page={page}")
        items = (data or {}).get("items", [])
        results.extend(items)
        if len(items) < 100 or page >= 10:
            break
        page += 1

    found = []
    for r in results:
        full = r["full_name"].lower()
        if full in curated_repos or full in excluded_repos:
            continue
        if r["owner"]["login"].lower() in excluded_owners:
            continue
        found.append(r)
    return found


def render_discovered(repos, topic):
    if not repos:
        return f"_No other repositories tagged `{topic}` yet._\n"
    lines = [
        "| Project | What it does | Links | Updated |",
        "| --- | --- | :---: | --- |",
    ]
    for r in repos:
        lines.append(
            f"| **[{cell(r['name'])}]({r['html_url']})**<br><sub>by {cell(r['owner']['login'])}</sub> | "
            f"{cell(r.get('description') or '')} | "
            f'<a href="{r["html_url"]}"><img src="{CODE_BADGE}" alt="Code"></a> | '
            f"{fmt_date(r.get('pushed_at'))} |"
        )
    return "\n".join(lines) + "\n"


def main():
    settings, projects = load_data()
    topic = settings.get("topic", "datafeel")
    curated_repos = {p["repo"].lower() for p in projects}

    body = [
        "## Featured projects\n",
        render_curated(projects),
        f"## More projects tagged `{topic}`\n",
        f"Found automatically from repositories with the "
        f"[`{topic}` topic](https://github.com/topics/{topic}).\n",
        render_discovered(discover(settings, curated_repos), topic),
        f"_List last rebuilt on {dt.date.today().isoformat()}._\n",
    ]
    generated = "\n".join(body)

    readme = README_FILE.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(readme):
        sys.exit(f"README.md must contain {START} and {END} markers.")
    new_readme = pattern.sub(lambda _: f"{START}\n{generated}\n{END}", readme)

    # Ignore the rebuild date when deciding whether anything changed,
    # so the workflow doesn't commit every run just to bump the date.
    date_line = re.compile(r"_List last rebuilt on [0-9-]+\._")
    if date_line.sub("", new_readme) != date_line.sub("", readme):
        README_FILE.write_text(new_readme, encoding="utf-8")
        print("README.md updated.")
    else:
        print("README.md already up to date.")


if __name__ == "__main__":
    main()
