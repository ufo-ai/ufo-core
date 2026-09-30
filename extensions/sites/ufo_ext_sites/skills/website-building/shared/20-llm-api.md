# Runtime LLM for Websites

A site's backend can call the Anthropic API at runtime (a chat box, on-the-fly summaries) over the sandbox's egress proxy. There is no build-time media-generation API — author visual assets directly with SVG, CSS, and considered layout (see `design-foundations`), or use assets the user provided.

## Runtime LLM — Anthropic over egress

A backend started with `start_server` / `publish_website` (see `19-backend.md`) can call the Anthropic API. The sandbox carries `ANTHROPIC_API_KEY` in its environment and routes outbound calls through the egress proxy.

```python
from anthropic import Anthropic

client = Anthropic()  # reads ANTHROPIC_API_KEY from the sandbox env
message = client.messages.create(
    model="claude-opus-5-5",
    max_tokens=16000,
    messages=[{"role": "user", "content": "Hello"}],
)
reply = next(block.text for block in message.content if block.type == "text")
```

Install the SDK in your server's dependencies (`anthropic` / `@anthropic-ai/sdk`) — it is not baked into the image.

Use a current model ID (`claude-opus-5-5` is the deploy default) rather than hardcoding a list that drifts. Its thinking counts toward `max_tokens`, so size `max_tokens` for thinking plus the reply, and read the reply from the `text` block, not `content[0]`.

**Egress is live only while the turn is running.** Outbound calls from the served app work during the active turn; once the turn ends, the sandbox's egress authorization is revoked. Build runtime-LLM features for in-session use, not for a site that must keep calling the API after you hand back.
