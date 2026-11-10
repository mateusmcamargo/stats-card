#!/usr/bin/env python3
"""Fetch GitHub stats via GraphQL and render static SVG cards (dark and light)."""

import datetime
import json
import os
import sys
import urllib.request

API = "https://api.github.com/graphql"
WIDTH = 480
PAD = 24
ROW = 22
ACCENT = "#ff79c6"
THEMES = {
    "dark": {"bg": "#101017", "fg": "#f8f8f2"},
    "light": {"bg": "#f8f8f2", "fg": "#101017"},
}

QUERY = """
query($login: String!, $after: String) {
    user(login: $login) {
        login
        pullRequests { totalCount }
        issues { totalCount }
        contributionsCollection {
            totalCommitContributions
            restrictedContributionsCount
            contributionCalendar {
                totalContributions
                weeks { contributionDays { date contributionCount } }
            }
        }
        repositories(first: 100, after: $after, ownerAffiliations: OWNER,
                     isFork: false, privacy: PUBLIC) {
            totalCount
            pageInfo { hasNextPage endCursor }
            nodes {
                stargazerCount
                languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
                    edges { size node { name } }
                }
            }
        }
    }
}
"""


def gql(token, login, after=None):
    """Run the GraphQL query and return the user object."""
    body = json.dumps({"query": QUERY, "variables": {"login": login, "after": after}}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": "bearer " + token,
        "Content-Type": "application/json",
        "User-Agent": "stats-card",
    })
    with urllib.request.urlopen(req) as res:
        data = json.load(res)
    if "errors" in data:
        sys.exit("GraphQL error: " + json.dumps(data["errors"]))
    return data["data"]["user"]


def fetch(token, login):
    """Collect all data, paginating the repository list."""
    viewer = gql(token, login)
    repos = list(viewer["repositories"]["nodes"])
    page = viewer["repositories"]["pageInfo"]
    while page["hasNextPage"]:
        more = gql(token, login, page["endCursor"])["repositories"]
        repos += more["nodes"]
        page = more["pageInfo"]
    return viewer, repos


def streaks(calendar):
    """Return (current, longest) streaks from the contribution calendar."""
    days = [d for w in calendar["weeks"] for d in w["contributionDays"]]
    days.sort(key=lambda d: d["date"])
    longest = run = 0
    for d in days:
        run = run + 1 if d["contributionCount"] > 0 else 0
        longest = max(longest, run)
    # Current streak: walk back from today; an empty today does not break it
    today = datetime.date.today().isoformat()
    days = [d for d in days if d["date"] <= today]
    current = 0
    for i, d in enumerate(reversed(days)):
        if d["contributionCount"] > 0:
            current += 1
        elif i > 0:
            break
    return current, longest


def languages(repos):
    """Return the top 5 languages as (name, percent) tuples."""
    sizes = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            sizes[name] = sizes.get(name, 0) + edge["size"]
    total = sum(sizes.values()) or 1
    top = sorted(sizes.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return [(name, size * 100 / total) for name, size in top]


def render(stats, theme):
    """Build a terminal-style SVG body and fill the template for one theme."""
    out = []
    step = 20
    bar_x, bar_w = 130, 250

    # Title bar with square window buttons
    for i in range(3):
        cls = "cursor" if i == 0 else "btn"
        out.append('    <rect class="{}" x="{}" y="10" width="9" height="9"/>'.format(cls, 16 + i * 16))
    out.append('    <text class="strong mid" x="{}" y="19">{}@github: ~</text>'.format(WIDTH // 2, stats["login"]))
    out.append('    <line class="rule" x1="0" y1="30" x2="{}" y2="30"/>'.format(WIDTH))
    y = 56

    def prompt(cmd):
        nonlocal y
        out.append('    <text x="{}" y="{}"><tspan class="accent">$</tspan> {}</text>'.format(PAD, y, cmd))
        y += step

    def heading(title):
        nonlocal y
        out.append('    <text class="strong" x="{}" y="{}"># {}</text>'.format(PAD, y, title))
        y += step

    def row(label, value):
        nonlocal y
        out.append('    <text x="{}" y="{}">{}</text>'.format(PAD, y, label))
        out.append('    <text class="strong end" x="{}" y="{}">{}</text>'.format(WIDTH - PAD, y, value))
        y += step

    prompt("stats --user " + stats["login"])
    y += 6
    heading("totals")
    for label, value in stats["totals"]:
        row(label, "{:,}".format(value))
    y += 8

    heading("languages")
    for name, pct in stats["langs"]:
        out.append('    <text x="{}" y="{}">{}</text>'.format(PAD, y, name))
        out.append('    <rect class="track" x="{}.5" y="{}.5" width="{}" height="10"/>'.format(bar_x, y - 10, bar_w))
        out.append('    <rect class="bar" x="{}" y="{}" width="{}" height="11"/>'.format(
            bar_x + 1, y - 10, max(round((bar_w - 1) * pct / 100), 2)))
        out.append('    <text class="strong end" x="{}" y="{}">{:.1f}%</text>'.format(WIDTH - PAD, y, pct))
        y += step
    y += 8

    heading("streak")
    for label, value in stats["streak"]:
        row(label, value)
    y += 8

    # Final prompt with a block cursor
    out.append('    <text x="{}" y="{}"><tspan class="accent">$</tspan></text>'.format(PAD, y))
    out.append('    <rect class="cursor" x="{}" y="{}" width="8" height="15"/>'.format(PAD + 16, y - 12))

    height = y + 20
    tpl = open(os.path.join(os.path.dirname(__file__), "template.svg")).read()
    values = {
        "width": WIDTH, "height": height,
        "inner_w": WIDTH - 1, "inner_h": height - 1,
        "bg": theme["bg"], "fg": theme["fg"], "accent": ACCENT,
        "body": "\n".join(out),
    }
    for key, val in values.items():
        tpl = tpl.replace("{{" + key + "}}", str(val))
    return tpl.rstrip("\n")


def main():
    token = os.environ.get("STATS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("Set STATS_TOKEN or GITHUB_TOKEN")
    # The Actions token is an app token, so query a named user instead of the viewer
    login = os.environ.get("STATS_USER") or os.environ.get("GITHUB_REPOSITORY_OWNER")
    if not login:
        sys.exit("Set STATS_USER (or GITHUB_REPOSITORY_OWNER)")
    viewer, repos = fetch(token, login)
    col = viewer["contributionsCollection"]
    current, longest = streaks(col["contributionCalendar"])
    stats = {
        "login": login,
        "totals": [
            ("commits (last year)", col["totalCommitContributions"] + col["restrictedContributionsCount"]),
            ("pull requests", viewer["pullRequests"]["totalCount"]),
            ("issues", viewer["issues"]["totalCount"]),
            ("stars", sum(r["stargazerCount"] for r in repos)),
            ("repositories", viewer["repositories"]["totalCount"]),
        ],
        "langs": languages(repos),
        "streak": [
            ("contributions", "{:,}".format(col["contributionCalendar"]["totalContributions"])),
            ("current streak", "{} days".format(current)),
            ("longest streak", "{} days".format(longest)),
        ],
    }
    os.makedirs("assets", exist_ok=True)
    for name, theme in THEMES.items():
        # Write without a trailing newline (project convention)
        with open("assets/stats-{}.svg".format(name), "w", newline="") as f:
            f.write(render(stats, theme).rstrip("\n"))


if __name__ == "__main__":
    main()