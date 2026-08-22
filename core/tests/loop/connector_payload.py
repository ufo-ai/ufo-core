"""One connector search result in the payload shape behind #282, and the tokens a window of them
really costs.

The field set and URL templates are GitHub's code-search response verbatim (the shape Composio's
`GITHUB_SEARCH_CODE` returns), pointed at this repository: URL-dense JSON, which is what a
connector-heavy window is made of. `CONNECTOR_WINDOW_TOKENS` is what Anthropic's
`/v1/messages/count_tokens` charges for exactly what `connector_window` builds, measured on
`claude-opus-4-8` — the measured truth the compaction estimate is held against.
"""

import json

from ufo.models.interface import Message, ToolResultBlock, ToolUseBlock

OWNER = "metalcraftai"
REPOSITORY = "ufo"
FULL_NAME = f"{OWNER}/{REPOSITORY}"
REPOSITORY_ID = 1207685915
COMMIT = "9352989ac1f0e4b7d2a5c8e13f6b0d94a7c25e38"
BLOB = "6c034babde80c8560748225259427ac7449c2e4e"
REPOSITORY_URL_TEMPLATES = (
    ("forks_url", "forks"),
    ("keys_url", "keys{/key_id}"),
    ("collaborators_url", "collaborators{/collaborator}"),
    ("teams_url", "teams"),
    ("hooks_url", "hooks"),
    ("issue_events_url", "issues/events{/number}"),
    ("events_url", "events"),
    ("assignees_url", "assignees{/user}"),
    ("branches_url", "branches{/branch}"),
    ("tags_url", "tags"),
    ("blobs_url", "git/blobs{/sha}"),
    ("git_tags_url", "git/tags{/sha}"),
    ("git_refs_url", "git/refs{/sha}"),
    ("trees_url", "git/trees{/sha}"),
    ("statuses_url", "statuses/{sha}"),
    ("languages_url", "languages"),
    ("stargazers_url", "stargazers"),
    ("contributors_url", "contributors"),
    ("subscribers_url", "subscribers"),
    ("subscription_url", "subscription"),
    ("commits_url", "commits{/sha}"),
    ("git_commits_url", "git/commits{/sha}"),
    ("comments_url", "comments{/number}"),
    ("issue_comment_url", "issues/comments{/number}"),
    ("contents_url", "contents/{+path}"),
    ("compare_url", "compare/{base}...{head}"),
    ("merges_url", "merges"),
    ("archive_url", "{archive_format}{/ref}"),
    ("downloads_url", "downloads"),
    ("issues_url", "issues{/number}"),
    ("pulls_url", "pulls{/number}"),
    ("milestones_url", "milestones{/number}"),
    ("notifications_url", "notifications{?since,all,participating}"),
    ("labels_url", "labels{/name}"),
    ("releases_url", "releases{/id}"),
    ("deployments_url", "deployments"),
)
OWNER_URL_TEMPLATES = (
    ("followers_url", "followers"),
    ("following_url", "following{/other_user}"),
    ("gists_url", "gists{/gist_id}"),
    ("starred_url", "starred{/owner}{/repo}"),
    ("subscriptions_url", "subscriptions"),
    ("organizations_url", "orgs"),
    ("repos_url", "repos"),
    ("events_url", "events{/privacy}"),
    ("received_events_url", "received_events"),
)
CONNECTOR_SEARCH_RESULT: dict[str, object] = {
    "successful": True,
    "data": {
        "total_count": 15800,
        "incomplete_results": False,
        "items": [
            {
                "name": "compaction.py",
                "path": "core/src/ufo/loop/compaction.py",
                "sha": BLOB,
                "url": (
                    f"https://api.github.com/repositories/{REPOSITORY_ID}"
                    f"/contents/core/src/ufo/loop/compaction.py?ref={COMMIT}"
                ),
                "git_url": f"https://api.github.com/repositories/{REPOSITORY_ID}/git/blobs/{BLOB}",
                "html_url": (
                    f"https://github.com/{FULL_NAME}/blob/{COMMIT}/core/src/ufo/loop/compaction.py"
                ),
                "repository": {
                    "id": REPOSITORY_ID,
                    "node_id": "R_kgDOR_vTGw",
                    "name": REPOSITORY,
                    "full_name": FULL_NAME,
                    "private": True,
                    "owner": {
                        "login": OWNER,
                        "id": 39856742,
                        "node_id": "MDQ6VXNlcjM5ODU2NzQy",
                        "avatar_url": "https://avatars.githubusercontent.com/u/39856742?v=4",
                        "gravatar_id": "",
                        "url": f"https://api.github.com/users/{OWNER}",
                        "html_url": f"https://github.com/{OWNER}",
                        **{
                            field: f"https://api.github.com/users/{OWNER}/{suffix}"
                            for field, suffix in OWNER_URL_TEMPLATES
                        },
                        "type": "Organization",
                        "user_view_type": "public",
                        "site_admin": False,
                    },
                    "html_url": f"https://github.com/{FULL_NAME}",
                    "description": (
                        "Window-triggered transcript compaction: a deterministic compression "
                        "pipeline that keeps a long turn under the model's real context window."
                    ),
                    "fork": False,
                    "url": f"https://api.github.com/repos/{FULL_NAME}",
                    **{
                        field: f"https://api.github.com/repos/{FULL_NAME}/{suffix}"
                        for field, suffix in REPOSITORY_URL_TEMPLATES
                    },
                },
                "score": 1.0,
            }
        ],
    },
}
CONNECTOR_SEARCH_RESULT_JSON = json.dumps(CONNECTOR_SEARCH_RESULT, separators=(",", ":"))
CONNECTOR_WINDOW_ROUNDS = 80
CONNECTOR_WINDOW_TOKENS = 182_013


def connector_window() -> tuple[Message, ...]:
    """A turn that searched the same connector `CONNECTOR_WINDOW_ROUNDS` times — the #282 shape,
    one tool_use and its result per round."""
    messages: list[Message] = [Message(role="user", content="find every use of the constant")]
    for index in range(CONNECTOR_WINDOW_ROUNDS):
        messages.append(
            Message(
                role="assistant",
                content=(
                    ToolUseBlock(
                        id=f"toolu_{index}",
                        name="GITHUB_SEARCH_CODE",
                        input={"q": "CHARS_PER_TOKEN in:file", "page": index + 1},
                    ),
                ),
            )
        )
        messages.append(
            Message(
                role="user",
                content=(
                    ToolResultBlock(
                        tool_use_id=f"toolu_{index}", content=CONNECTOR_SEARCH_RESULT_JSON
                    ),
                ),
            )
        )
    return tuple(messages)
