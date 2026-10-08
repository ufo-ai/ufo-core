from ufo.sdk.context import AgentChange, ExtensionContext, JsonValue
from ufo.sdk.sandbox import WORKSPACE_DIR
from ufo_ext_sample.surfaces import SURFACE_NAME

JOB_NAME = "sample_tick"
JOB_KEY = "job:ran"
JOB_SURFACE_KEY = "job:surface_links"
TRAJECTORY_KEY = "job:trajectories"
PROPOSAL_KEY = "job:proposal"
JOB_WORKSPACE_KEY = "job:workspace_file"
JOB_WORKSPACE_REL = "sample-job/tick.txt"
JOB_WORKSPACE_BODY = "the sample job wrote this off-turn"
JOB_PROBE_KEY = "job:probe"
JOB_PROBE_COMMAND = f"cat {WORKSPACE_DIR}/{JOB_WORKSPACE_REL}"
PROPOSAL_SUFFIX = "\nBe concise."


async def tick(ctx: ExtensionContext) -> None:
    minted: list[JsonValue] = [slot for slot in sorted(ctx.credentials.minted)]
    await ctx.store.put(JOB_KEY, {"ran": True, "home_url": ctx.home_url(), "minted": minted})
    linked = await ctx.installations.linked_members(SURFACE_NAME)
    links: list[JsonValue] = [link for link in sorted(linked.values())]
    await ctx.store.put(JOB_SURFACE_KEY, {"linked": links})
    if ctx.corpus is None:
        return
    trajectories = await ctx.trajectories()
    await ctx.store.put(TRAJECTORY_KEY, {"count": len(trajectories)})
    if not trajectories:
        return
    target = trajectories[0]
    ref = await ctx.propose_change(
        AgentChange(
            agent_id=target.agent_id,
            new_prompt=target.agent_prompt + PROPOSAL_SUFFIX,
            from_digest=target.agent_prompt_digest,
        )
    )
    await ctx.store.put(PROPOSAL_KEY, {"proposal_id": str(ref.proposal_id)})
    if ctx.files is None:
        return
    path = await ctx.files.write(
        target.conversation_id, JOB_WORKSPACE_REL, JOB_WORKSPACE_BODY.encode()
    )
    await ctx.store.put(JOB_WORKSPACE_KEY, {"path": path})
    if ctx.probes is None:
        return
    probed = await ctx.probes.run(target.conversation_id, JOB_PROBE_COMMAND)
    await ctx.store.put(JOB_PROBE_KEY, {"stdout": probed.stdout, "exit_code": probed.exit_code})
