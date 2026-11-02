"""Rebuild the project list in README.md.

Reads curated projects from projects.yml, checks each repo on GitHub,
discovers other repos tagged with the configured topic, and rewrites the
section of README.md between the PROJECTS markers.

"""

import datetime as dt
import html
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
        for field in ("name", "event", "description"):
            if not p.get(field):
                sys.exit(f"projects.yml entry {i} is missing required field '{field}'.")
        if p.get("repo") and p["repo"].count("/") != 1:
            sys.exit(f"projects.yml entry {i}: repo must look like 'owner/name', got '{p['repo']}'.")
    return settings, projects


def fmt_date(iso):
    # Non-breaking hyphens keep the date on one line in narrow table columns.
    return iso[:10].replace("-", "\u2011") if iso else ""


CODE_BADGE = "https://img.shields.io/badge/Code-24292f?style=flat&logo=github&logoColor=white"
DEMO_BADGE = "https://img.shields.io/badge/Demo-2E97A8?style=flat"
PAPER_BADGE = "https://img.shields.io/badge/Paper-6E7781?style=flat"
ARCHIVED_BADGE = "https://img.shields.io/badge/Archived-8a8a8a?style=flat"

# Fixed column widths keep every row aligned, across all events.
COLUMNS = [
    ("Project", "16%", "left"),
    ("What it does", "44%", "left"),
    ("Team", "16%", "left"),
    ("Links", "10%", "center"),
    ("Updated", "14%", "left"),
]


def esc(text):
    """Escape text for HTML and collapse whitespace."""
    return html.escape(" ".join(str(text).split()))


def link(url, text):
    return f'<a href="{html.escape(url, quote=True)}">{text}</a>'


def badge(url, img, alt):
    return link(url, f'<img src="{img}" alt="{alt}">')


def render_member(m):
    name = esc(m.get("name", ""))
    if m.get("github"):
        name = link(f"https://github.com/{m['github']}", name)
    if m.get("linkedin"):
        name += " <sub>" + link(m["linkedin"], "LinkedIn") + "</sub>"
    return name


def row(cells):
    out = ["  <tr>"]
    for (_, _, align), content in zip(COLUMNS, cells):
        out.append(f'    <td align="{align}" valign="middle">{content}</td>')
    out.append("  </tr>")
    return out


def group_row(title, note=""):
    note = f"<br><sub>{note}</sub>" if note else ""
    return [
        "  <tr>",
        f'    <td colspan="{len(COLUMNS)}"><b>{title}</b>{note}</td>',
        "  </tr>",
    ]


def curated_rows(projects):
    events = []
    for p in projects:
        if p["event"] not in events:
            events.append(p["event"])

    lines = []
    for event in events:
        lines += group_row(esc(event))
        for p in (p for p in projects if p["event"] == event):
            repo = p.get("repo")
            meta = gh_get(f"/repos/{repo}") if repo else None
            code_url = f"https://github.com/{repo}" if repo else ""

            name = f"<b>{esc(p['name'])}</b>"
            # Link the name to the code, or to the paper or demo when there's no repo.
            target = code_url if meta else (p.get("paper") or p.get("demo"))
            project = link(target, name) if target else name
            tags = []
            if p.get("award"):
                tags.append(esc(p["award"]))
            if p.get("track"):
                tags.append(f"{esc(p['track'])} track")
            if tags:
                project += "<br><sub>" + " · ".join(tags) + "</sub>"

            team = "<br>".join(render_member(m) for m in p.get("team") or [])
            if p.get("affiliation"):
                team += ("<br>" if team else "") + f"<sub>{esc(p['affiliation'])}</sub>"

            links = []
            if repo and meta is None:
                links.append("<sub>Repo unavailable</sub>")
            elif meta:
                links.append(badge(code_url, CODE_BADGE, "Code"))
                if meta.get("archived"):
                    links.append(f'<img src="{ARCHIVED_BADGE}" alt="Archived">')
            if p.get("demo"):
                links.append(badge(p["demo"], DEMO_BADGE, "Demo"))
            if p.get("paper"):
                links.append(badge(p["paper"], PAPER_BADGE, "Paper"))

            updated = fmt_date(meta.get("pushed_at")) if meta else ""
            lines += row([project, esc(p["description"]), team, "<br>".join(links), updated])
    return lines


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


def discovered_rows(repos, topic):
    if not repos:
        return []
    title = f'Also tagged {link(f"https://github.com/topics/{topic}", f"<code>{esc(topic)}</code>")}'
    lines = group_row(title, "Found automatically from repositories with this topic.")
    for r in repos:
        owner = r["owner"]["login"]
        lines += row([
            link(r["html_url"], f"<b>{esc(r['name'])}</b>"),
            esc(r.get("description") or ""),
            link(f"https://github.com/{owner}", esc(owner)),
            badge(r["html_url"], CODE_BADGE, "Code"),
            fmt_date(r.get("pushed_at")),
        ])
    return lines


def render_table(projects, repos, topic):
    if not projects and not repos:
        return "_No projects yet._\n"
    header = ["<table>", "  <tr>"]
    for title, width, align in COLUMNS:
        header.append(f'    <th width="{width}" align="{align}">{title}</th>')
    header.append("  </tr>")
    body = curated_rows(projects) + discovered_rows(repos, topic)
    return "\n".join(header + body + ["</table>"]) + "\n"


def main():
    settings, projects = load_data()
    topic = settings.get("topic", "datafeel")
    curated_repos = {p["repo"].lower() for p in projects if p.get("repo")}

    repos = discover(settings, curated_repos)
    body = [
        "## Projects\n",
        render_table(projects, repos, topic),
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
