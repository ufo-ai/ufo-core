"""The first customization of a row-less shipped page and a second change to a bound page.

The first case starts without a site row: the app agent copies its home skill's project, edits it,
deploys the source, and binds the result. The second seed stores and binds the project a deploy
carries, then asks for two changes so each edit must start from the stored source. Bind the run to
the app agent under test (`--agent chat`) so the wording is read by its own agent.

The fixture is the two source files a page project carries. The deploy tool supplies the config and
SDK and runs Vite, so the trajectory must contain no model-run build or dependency installation.
Every grader reads either that trajectory or the bytes left on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from re import compile as re_compile
from uuid import UUID, uuid4, uuid5

import sqlalchemy as sa
import ufo_ext_app_chat
from ufo_ext_sites.source import PROJECT_SOURCE
from ufo_ext_sites.store import SiteFile, SourceManifest, hosted_site
from ufo_ext_web.audience import EXTENSION_WEB
from ufo_ext_web.surface import HOMEPAGE_SETTLED_PREFIX

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilitySeed,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    ToolInvocation,
)
from evals.harness.scorers import (
    combine,
    required_tools_scorer,
    restraint_scorer,
    skill_scorer,
)
from ufo.blob import WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.sdk.context import ScopedStore

WORKFLOW_WAIT_SECONDS = 900.0
HOME_SKILL = "app-chat-home"
SITE_SKILL = "website-building"
APP_SLUG = "chat"
SITE_NAME = "chat-home"
SITE_PORT = 3000
SITE_VISIBILITY = "workspace"
DEPLOY_GENERATION = 1
SEED_SURFACE = "eval"
SEED_NAMESPACE = UUID("6f9d7c1e-0b3a-4d2f-9c14-8a7b5e2d0f31")
SKILL_DIR = Path(ufo_ext_app_chat.__file__).parent / "skills" / HOME_SKILL
PROJECT_FIXTURE = {
    f"src/{PROJECT_SOURCE}": SKILL_DIR / PROJECT_SOURCE,
    "src/index.html": SKILL_DIR / "index.html",
}
PAGE_ENTRY = "index.html"
MEDIA_TYPES = {".html": "text/html", ".ts": "text/plain", ".tsx": "text/plain"}
DIST_DIR = "dist"
EDIT_TOOLS = ("write", "edit")
DEPLOY_TOOL = "action:site:deploy_website"
READ_TOOL = "object_get"
BASH_TOOL = "bash"
HOMEPAGE_TOOL = "action:agent:set_homepage"
HASHED_SCRIPT = re_compile(r'src="[^"]*assets/[^"/]+\.js"')
INSTALL_COMMANDS = ("npm install", "npm i ", "npm add", "yarn add", "pnpm add", "pnpm install")
BUILD_COMMANDS = ("vite build", "npm run build", "pnpm build", "pnpm run build", "yarn build")
FIRST_CHANGE = "Add a search box above the transcript on the chat homepage."
SECOND_CHANGE = (
    "Add a search box above the transcript on the chat homepage. Once it is live, make its "
    "placeholder read Search this conversation and put the page back up."
)


async def _settle_homepage_jobs(workspace_id: UUID) -> None:
    async with workspace_tx() as connection:
        agent_ids = tuple(
            (
                await connection.execute(
                    sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
                )
            ).scalars()
        )
    store = ScopedStore(extension=EXTENSION_WEB)
    for agent_id in agent_ids:
        await store.put(f"{HOMEPAGE_SETTLED_PREFIX}{agent_id}", "eval")


def _rowless_page() -> CapabilitySeed:
    async def seed(workspace_id: UUID, _agent_id: UUID, _blob: WorkspaceBlobStore) -> None:
        await _settle_homepage_jobs(workspace_id)

    return seed


def _deployed_page() -> CapabilitySeed:
    """Store the page project as the target agent's homepage site, the shape a `dist` deploy leaves
    once its `closeBundle` has carried the source into the output. Every case owns this row, so the
    seed rewrites it rather than adding to it."""

    async def seed(workspace_id: UUID, agent_id: UUID, blob: WorkspaceBlobStore) -> None:
        await _settle_homepage_jobs(workspace_id)
        files = _deployed_tree()
        conversation_id = uuid5(SEED_NAMESPACE, f"{workspace_id}/{SITE_NAME}")
        root = f"sites/{conversation_id}/{SITE_NAME}/{uuid4().hex}/"
        for path, body in files.items():
            await blob.put(f"{root}{path}", body)
        manifest = SourceManifest(
            root=root,
            files={
                path: SiteFile(
                    size=len(body), media_type=_media_type(path), sha256=sha256(body).hexdigest()
                )
                for path, body in files.items()
            },
        )
        async with workspace_tx() as connection:
            member_id = (
                await connection.execute(
                    sa.select(tables.member.c.id)
                    .where(
                        tables.member.c.workspace_id == workspace_id,
                        tables.member.c.is_admin.is_(True),
                    )
                    .order_by(tables.member.c.created_at)
                    .limit(1)
                )
            ).scalar_one()
            await connection.execute(
                sa.delete(hosted_site).where(
                    hosted_site.c.workspace_id == workspace_id,
                    hosted_site.c.name == SITE_NAME,
                )
            )
            held = (
                await connection.execute(
                    sa.select(tables.conversation.c.id).where(
                        tables.conversation.c.id == conversation_id
                    )
                )
            ).scalar_one_or_none()
            if held is None:
                await connection.execute(
                    sa.insert(tables.conversation).values(
                        id=conversation_id,
                        workspace_id=workspace_id,
                        agent_id=agent_id,
                        surface=SEED_SURFACE,
                        queue_key=f"{SEED_SURFACE}:{SITE_NAME}:{conversation_id}",
                        member_id=member_id,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
            await connection.execute(
                sa.insert(hosted_site).values(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    name=SITE_NAME,
                    port=SITE_PORT,
                    visibility=SITE_VISIBILITY,
                    creator_member_id=member_id,
                    generation=uuid4(),
                    deploy_generation=DEPLOY_GENERATION,
                    homepage_agent_id=agent_id,
                    source_manifest=manifest.model_dump_json(),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    return seed


def _deployed_tree() -> dict[str, bytes]:
    tree: dict[str, bytes] = {}
    for path, source in PROJECT_FIXTURE.items():
        if not source.is_file():
            raise RuntimeError(f"this fixture's {path} is {source}, which does not exist")
        tree[path] = source.read_bytes()
    return tree


def _media_type(path: str) -> str:
    suffix = Path(path).suffix
    if suffix not in MEDIA_TYPES:
        raise RuntimeError(f"the fixture carries {path}, whose media type it has none for")
    return MEDIA_TYPES[suffix]


def _pull_before_edit_scorer() -> Grader:
    """The read that makes an edit land on the deployed page: a successful `object_get` before the
    first write into the project. Editing first is editing whatever the sandbox happened to hold."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        pulls = tuple(
            index
            for index, call in enumerate(output.calls)
            if call.name == READ_TOOL and call.succeeded
        )
        if not pulls:
            return CapabilityVerdict(False, f"never {READ_TOOL} the homepage site")
        edits = tuple(
            index
            for index, call in enumerate(output.calls)
            if call.name in EDIT_TOOLS and _names_the_entry(call)
        )
        if not edits:
            return CapabilityVerdict(False, f"never edited {PROJECT_SOURCE}")
        if pulls[0] > edits[0]:
            return CapabilityVerdict(False, f"edited {PROJECT_SOURCE} before reading the site")
        return CapabilityVerdict(True, "read the deployed source before editing it")

    return DescribedGrader("a successful object_get precedes the first project edit", grade)


def _names_the_entry(call: ToolInvocation) -> bool:
    named = call.input.get("file_path")
    return isinstance(named, str) and named.endswith(PROJECT_SOURCE)


def _tool_owns_the_build_scorer() -> Grader:
    """The deploy tool owns the page build, with no project install or model-run build."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        commands = tuple(call for call in output.calls if call.name == BASH_TOOL)
        installs = tuple(
            call
            for call in commands
            if any(named in str(call.input.get("command", "")) for named in INSTALL_COMMANDS)
        )
        if installs:
            return CapabilityVerdict(
                False, f"installed into the project: {installs[0].input.get('command')}"
            )
        builds = tuple(
            call
            for call in commands
            if any(named in str(call.input.get("command", "")) for named in BUILD_COMMANDS)
        )
        if builds:
            return CapabilityVerdict(False, "ran the deploy tool's vite build itself")
        return CapabilityVerdict(True, "left the build and dependency surface to deploy_website")

    return DescribedGrader("deploy_website owns the build and nothing is installed locally", grade)


def _deployed_source_scorer(deploys: int) -> Grader:
    """The source project handed to deploy_website, which builds and hosts it."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        landed = tuple(
            call
            for call in output.calls
            if call.call == DEPLOY_TOOL
            and call.succeeded
            and call.arguments.get("site_name") == SITE_NAME
        )
        if len(landed) < deploys:
            return CapabilityVerdict(
                False, f"deployed {SITE_NAME} {len(landed)} time(s), needs {deploys}"
            )
        for call in landed:
            named = str(call.arguments.get("project_path", ""))
            if Path(named).name == DIST_DIR:
                return CapabilityVerdict(False, f"deployed the build directory {named}")
        return CapabilityVerdict(True, f"deployed source {len(landed)} time(s)")

    return DescribedGrader(f"{deploys} deploy(s) of source under {SITE_NAME}", grade)


def _homepage_names_the_app_scorer() -> Grader:
    """The bind lands on this app's own agent: the action is dispatched on `agent/<name>`, so a
    name other than the app's would rewrite another agent's page."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        binds = tuple(
            call for call in output.calls if call.call == HOMEPAGE_TOOL and call.succeeded
        )
        if not binds:
            return CapabilityVerdict(False, "no homepage was bound")
        targets = sorted({str(call.input.get("name", "")) for call in binds})
        if targets != [APP_SLUG]:
            named = ", ".join(target or "(no agent)" for target in targets)
            return CapabilityVerdict(False, f"bound the homepage of {named}, not {APP_SLUG}")
        return CapabilityVerdict(True, f"bound {APP_SLUG}'s own homepage")

    return DescribedGrader(f"the homepage bind targets the {APP_SLUG} agent", grade)


def _pull_between_deploys_scorer() -> Grader:
    """The second change's own read: between two deploys the source of record moved, so the second
    edit starts from a fresh `object_get` rather than from the tree the first build left."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        deploys = tuple(
            index
            for index, call in enumerate(output.calls)
            if call.call == DEPLOY_TOOL and call.succeeded
        )
        if len(deploys) < 2:
            return CapabilityVerdict(
                False, f"deployed {len(deploys)} time(s); the second change needs another"
            )
        pulls = tuple(
            index
            for index, call in enumerate(output.calls)
            if call.name == READ_TOOL and call.succeeded
        )
        if not any(deploys[0] < pull < deploys[-1] for pull in pulls):
            return CapabilityVerdict(
                False, f"no {READ_TOOL} between the deploys, so the second edit reworked a copy"
            )
        return CapabilityVerdict(True, "read the site again before the second deploy")

    return DescribedGrader("the second change reads the site between the two deploys", grade)


@dataclass(frozen=True)
class _BuiltPage:
    """The page the build wrote, read off the host directory the turn's `/workspace` was served
    from — the bytes the deploy carried, not a claim about them."""

    async def __call__(self, output: CapabilityOutput) -> CapabilityVerdict:
        if output.workspace_dir is None:
            return CapabilityVerdict(False, "the case has no workspace directory to read")
        pages = sorted(output.workspace_dir.rglob(f"{DIST_DIR}/{PAGE_ENTRY}"))
        if not pages:
            return CapabilityVerdict(False, f"no {DIST_DIR}/{PAGE_ENTRY} under the workspace")
        if not HASHED_SCRIPT.search(pages[0].read_text(errors="replace")):
            return CapabilityVerdict(
                False, f"{pages[0]} names no hashed asset, so it is not a build's output"
            )
        carried = pages[0].parent / "src" / PROJECT_SOURCE
        if not carried.is_file():
            return CapabilityVerdict(
                False, f"the build carried no {carried.name}, so the page cannot be edited again"
            )
        return CapabilityVerdict(True, "the built page names a hashed asset over its own source")


def _built_page_scorer() -> Grader:
    return DescribedGrader(
        "the built page names a hashed asset and carries the project it was built from",
        _BuiltPage(),
    )


CASES = (
    CapabilityCase(
        "chat-home-add-control",
        FIRST_CHANGE,
        combine(
            skill_scorer(HOME_SKILL, SITE_SKILL),
            _tool_owns_the_build_scorer(),
            _deployed_source_scorer(1),
            _built_page_scorer(),
            required_tools_scorer((DEPLOY_TOOL, HOMEPAGE_TOOL), ((DEPLOY_TOOL, HOMEPAGE_TOOL),)),
            _homepage_names_the_app_scorer(),
        ),
        seed=_rowless_page(),
        digest_tag=f"app-home:{APP_SLUG}:actions:target:one-change:wait-{WORKFLOW_WAIT_SECONDS:g}",
    ),
    CapabilityCase(
        "chat-home-second-change",
        SECOND_CHANGE,
        combine(
            skill_scorer(HOME_SKILL, SITE_SKILL),
            _pull_before_edit_scorer(),
            _tool_owns_the_build_scorer(),
            _deployed_source_scorer(2),
            _pull_between_deploys_scorer(),
            _built_page_scorer(),
            restraint_scorer((HOMEPAGE_TOOL,)),
        ),
        seed=_deployed_page(),
        digest_tag=f"app-home:{APP_SLUG}:actions:target:second-change:wait-{WORKFLOW_WAIT_SECONDS:g}",
    ),
)
