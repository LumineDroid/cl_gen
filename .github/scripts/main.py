import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests
from gql import Client, gql
from gql.transport.requests import RequestsHTTPTransport

BRANCH = os.environ["BRANCH"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
INPUT_DATE = os.environ["START_DATE"]

ORG = "LumineDroid"
ALLOWED_REMOTE = "lumine"

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
MANIFEST_BASE = f"https://raw.githubusercontent.com/LumineDroid/platform_manifest/{BRANCH}/snippets"


def get_projects(file: str) -> list[str]:
    """Return a list of repo names from a manifest snippet (only `lumine` remote)."""
    url = f"{MANIFEST_BASE}/{file}.xml"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Error fetching manifest '{file}.xml': {e}")
        return []

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as e:
        print(f"Error parsing manifest '{file}.xml': {e}")
        return []

    repos: list[str] = []
    for project in root.findall(".//project"):
        remote = project.get("remote", "")
        name = project.get("name", "")

        if remote != ALLOWED_REMOTE or not name:
            continue

        repo = name.split("/")[-1]
        repos.append(repo)

    return repos


transport = RequestsHTTPTransport(
    url=GITHUB_GRAPHQL_URL,
    headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
    verify=True,
    retries=3,
)
client = Client(transport=transport, fetch_schema_from_transport=True)

COMMITS_QUERY = gql("""
query ($org: String!, $repo: String!, $branch: String!, $cursor: String) {
  repository(owner: $org, name: $repo) {
    ref(qualifiedName: $branch) {
      target {
        ... on Commit {
          history(first: 100, after: $cursor) {
            pageInfo {
              hasNextPage
              endCursor
            }
            edges {
              node {
                oid
                messageHeadline
                committedDate
                author {
                  name
                }
                url
              }
            }
          }
        }
      }
    }
  }
}
""")


def fetch_commits(repo: str, start_date: datetime, end_date: datetime) -> list[dict]:
    commits = []
    cursor = None

    while True:
        variables = {
            "org": ORG,
            "repo": repo,
            "branch": BRANCH,
            "cursor": cursor,
        }
        try:
            result = client.execute(COMMITS_QUERY, variable_values=variables)
        except Exception as e:
            print(f"Error fetching commits from {ORG}/{repo}: {e}")
            break

        repository = result.get("repository")
        if not repository or not repository.get("ref"):
            print(f"Warning: Branch '{BRANCH}' not found in '{ORG}/{repo}'. Skipping...")
            break

        history = repository["ref"]["target"]["history"]
        edges = history.get("edges", [])

        if not edges:
            print(f"No commits found in '{ORG}/{repo}'.")
            break

        stop_early = False
        for edge in edges:
            node = edge["node"]
            commit_date = datetime.strptime(
                node["committedDate"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)

            if commit_date < start_date:
                print(f"Reached commits older than start date in '{ORG}/{repo}'. Stopping...")
                stop_early = True
                break

            if commit_date <= end_date:
                commits.append({
                    "hash": node["oid"],
                    "link": node["url"],
                    "title": node["messageHeadline"],
                    "author": node["author"]["name"],
                    "date": node["committedDate"],
                })

        page_info = history["pageInfo"]
        if stop_early or not page_info["hasNextPage"]:
            break

        cursor = page_info["endCursor"]
        print(f"Fetched {len(edges)} commits from '{ORG}/{repo}', continuing pagination...")

    print(f"Total {len(commits)} commit(s) collected from '{ORG}/{repo}'.")
    return commits


end_date = datetime.now(timezone.utc)
start_date = datetime.strptime(INPUT_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)

print(f"Collecting commits from {start_date.date()} to {end_date.date()} on branch '{BRANCH}'")

all_commits: list[dict] = []
repos = get_projects("lumine")
print(f"Found {len(repos)} repo(s): {repos}")

for repo in repos:
    print(f"\nFetching commits from '{ORG}/{repo}'...")
    commits = fetch_commits(repo, start_date, end_date)
    all_commits.extend(commits)

all_commits.sort(key=lambda x: x["date"], reverse=True)

lines = []
current_date = None
for commit in all_commits:
    date_str = datetime.strptime(commit["date"], "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d")
    if date_str != current_date:
        lines.append(f"\n## {date_str}")
        current_date = date_str
    lines.append(
        f"[{commit['hash'][:7]}]({commit['link']}) {commit['title']} _(by {commit['author']})_  "
    )

with open("changelogs.mdx", "w") as f:
    f.write(
        "---\n"
        "title: Changelogs\n"
        "description: Find all the changes happened across the repositories of LumineDroid\n"
        "toc: false\n"
        "footer: false\n"
        "---\n\n"
    )
    f.write("\n".join(lines))
    f.write("\n")

print(f"\nDone. {len(all_commits)} total commit(s) written to changelogs.mdx.")
