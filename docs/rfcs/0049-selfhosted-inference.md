---
rfc: 0049
title: "Self-hosted inference for the agent fleet — GLM-5.3-Flash and DeepSeek-V4.1-Flash on AWS"
status: proposed
date: 2026-09-12
---

# Self-hosted inference on AWS

> Every model call this product makes goes to a third-party API. This RFC costs out serving
> `z-ai/glm-5.3-flash` and `deepseek/deepseek-v4.1-flash` — plus one small cheap model for
> background jobs — from our own GPUs on AWS. **The answer at current volume is that it costs
> 70x buying the tokens at our real utilization, and the gap closes only for a 100-person tenant
> on a heavily discounted fleet sized below its own burst rate.** The one tier close to
> break-even today is the small background model, about 1.7x away on a single L40S.

## Current state

- `ModelSpec` (`core/src/ufo/harness/models/spec.py:60`) and `ModelPrice`
  (`core/src/ufo/harness/models/pricing.py:15`) record prices in **micro-USD per million tokens**,
  one rate per class: `input`, `output`, `cache_read`, `cache_write_5m`, `cache_write_30m`,
  `cache_write_1h`. `150_000` is $0.15/Mtok.
- Both target models are registered only in the OpenRouter extension
  (`extensions/openrouter/ufo_ext_openrouter.py:966-1030`): `z-ai/glm-5.3-flash` at
  `ModelPrice(150_000, 500_000, 30_000, 0, 0)` — $0.15 in / $0.50 out / $0.03 cache read, and
  **no cache-write premium** — and `deepseek/deepseek-v4.1-flash` at
  `ModelPrice(220_000, 660_000, 7_000, 0, 0)`.
- Production runs `auto_model = "z-ai/glm-5.3-flash"` (`infra/envs/prod/ufo.tf:100`); testing runs
  `deepseek/deepseek-v4.1-flash` (`infra/envs/testing/ufo.tf:102`). Ambient and background jobs
  resolve to the default `gpt-5.6-luna` (`config.py:20`), $0.20/$1.20.
- `[models]` holds exactly five knobs (`config.py:94-125`). There is no base-URL or serving-pool
  knob. A self-hosted endpoint arrives as a key slot plus a `ModelSpec` per served model; the
  OpenRouter extension is the template, since it already builds OpenAI-wire clients against a
  configurable base.
- **Nothing about GPU hosting, vLLM, SGLang or self-hosted inference exists anywhere in the
  tree.** Case-insensitive searches for `vllm`, `sglang`, `triton`, `nvidia` return no runtime,
  config, IaC, doc, stub or comment. This is greenfield.

## What the workload actually looks like

Measured from Datadog, 2026-08-13 to 2026-09-12. Full derivation in `/workspace/usage-profile.md`.

| Class | 7d tokens | Share | Rate (t/s) |
|---|---|---|---|
| cache read | 15,348,962,952 | 88.58% | 25,379 |
| fresh input | 1,260,575,447 | 7.27% | 2,084 |
| cache write | 548,218,678 | 3.16% | 906 |
| output | 170,047,368 | 0.98% | 281 |
| **total** | **17,327,804,445** | 100% | **28,650** |

Read that as a compute statement, not a billing statement:

- **Prefill compute is 2,991 t/s** (fresh input + cache write). Cache reads are KV-cache hits and
  cost almost no prefill FLOPs, which is exactly why a hosted provider charges 0.1x for them.
- **Decode is 281 t/s.** The whole fleet generates 281 output tokens per second. At the brief's
  4-person basis, that is 70 output tokens/s/person, or ~0.4 tokens per second of human-visible
  writing.
- So the workload is **prefill-dominated by ~10.6x**, and one-tenth of what a decode-oriented
  sizing would assume.

Burst shape, the other half of the problem:

| Measure | Value | vs mean hour |
|---|---|---|
| mean hour | 103,207,638 tokens | 1.0x |
| median hour | 74,763,799 | 0.7x |
| p95 hour | 346,016,807 | 3.4x |
| peak hour (2026-09-10 22:00) | 916,420,507 | **8.9x** |
| peak 5 min | 135,332,316 (451,108 t/s) | **15.7x** |

True concurrency is tiny: `ufo.model_round_active` never exceeded **10** in seven days and
`ufo.turn_slot_wait_ms` is **0.0** in every bucket. There is no queue today, anywhere.

One caveat that inflates the mean: the peak lands on 2026-09-10 and that day alone is 5.84e9
tokens against a **median day of 1.64e9**. The mean is inflated ~1.5x by one non-recurring burst,
so the peak-to-mean ratio should not be read as a tenant burst pattern.

## The models

### GLM-5.3-Flash — 320B total, 18B active

Released 2026-08-26 by Z.ai, **MIT-licensed open weights**, 1,048,576-token context, first
natively multimodal model in the GLM-5 line
([deep dive, 2026-08-26](https://local-ai-zone.github.io/blog/glm-5-3-flash-deep-dive.html)). The
native FP8 checkpoint is **306-331 GiB**, so there is **no two-GPU entry point**: the floor is an
8-GPU Hopper-or-newer node
([YottaLabs comparison, 2026-09-11](https://www.yottalabs.ai/post/glm-5-3-flash-vs-deepseek-v4-flash-2026)).
Vendor serving recipe: `vllm serve zai-org/GLM-5.3-Flash --tensor-parallel-size 4
--kv-cache-dtype fp8 --speculative-config '{"method":"mtp","num_speculative_tokens":5}'
--tool-call-parser glm47` ([vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash)). NVIDIA
publishes Dynamo targets at 8xH200 aggregated or 8xH200 prefill + 8xH200 decode
([NVIDIA Dynamo recipe](https://docs.nvidia.com/dynamo/dev/recipes/glm-5-3-flash)).

Two hazards worth naming. First, the `glm5_next` architecture is **not yet in vLLM `main`**
(vllm#53906, sglang#36507) — support ships in **per-model container images**
(`vllm/vllm-openai:glm53-flash-x86_64-cu130`), making a third-party registry a production
dependency. Second, the tool-call parser name is load-bearing and silently destructive if wrong:
against this model's prompt-side ` thinking`, vLLM's `glm45` parser "discards the reply entirely"
([community NVFP4 card](https://huggingface.co/LibertAIDAI/GLM-5.3-Flash-NVFP4)).

### DeepSeek-V4.1-Flash — 552B backbone, 8B prefill / 16B decode active

MIT-licensed open weights on Hugging Face, up to 1M context, MoE with 1 shared and 384 routed
experts per layer and 6 routed active per token, activating only 8B during prefill and 16B during
decode ([Hugging Face card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash)). KV cache is
**890 bytes/token**, claimed ~4x smaller than the predecessor. Its predecessor V4-Flash shipped at
166.9 GB of safetensors; V4.1-Flash's 552B backbone puts it in the same 8-GPU H200 class as GLM,
with a two-GPU pod possible at reduced context.

**DeepSeek is the cheaper model on both sides of the ledger**: a smaller checkpoint to host *and*
a cheaper token to buy hosted. That inverts the usual instinct and is why its self-hosting case is
worse than GLM's, not better.

### The small background model

Dense, cheap, tool-call capable, one GPU: **Qwen3-4B** (~2.5 GB at Q4_K_M) or **Qwen3-8B**
(~5-6 GB), both Apache-2.0, 128K context, dual thinking/non-thinking modes; **Qwen3-1.7B**
(~1.4 GB) for the cheapest classification and title tier; **Qwen3-30B-A3B** (30B total, ~3B
active) as the step-up if extraction needs more nuance. All fit a single L40S (48 GB) comfortably.

Today's background tier is `gpt-5.6-luna`: 673,143,533 tokens over 7 days, of which 491.3e6 fresh
input, 146.2e6 cache read and 35.8e6 output.

## Serving stack

| Dimension | vLLM | SGLang |
|---|---|---|
| Prefix-heavy traffic | good | **better** — RadixAttention; 195 ms vs 310 ms p50 TTFT at 80% shared prefix, 50 concurrent ([Spheron, 2026-06-23](https://www.spheron.network/blog/vllm-vs-sglang-2026/)) |
| Large-MoE at scale | **better documented** — wide-EP and disaggregated prefill/decode on GB200 ([vLLM blog, 2026-02-03](https://vllm.ai/blog/2026-02-03-dsr1-gb200-part1)) | strong; within ~10% on MoE throughput |
| This model family | vendor per-model image | per-model image, less exercised |
| Blackwell | more mature; Eagle3 speculation | NVFP4 MoE kernels |

**Pick SGLang**, for one reason the measured data makes decisive: **88.6% of our tokens are
prefix-cache hits.** RadixAttention is what turns that number into capacity, and it is the same
mechanism that makes the hosted 0.1x cache-read discount possible. vLLM is the fallback if
SGLang's GLM-5.3-Flash support lags. Either way, pin a per-model image, not a floating tag.

## Performance gaps

Ranked by how much they move the answer.

1. **No published AWS-GPU throughput for GLM-5.3-Flash on either engine.** The only measured
   GLM-class H200 FP8 numbers found are for the previous model, GLM-5.2, in SGLang's cookbook:
   **197 tok/s/GPU at concurrency 1 and 813 tok/s/GPU at concurrency 16** for 8K-in/1K-out
   ([SGLang cookbook](https://lmsysorg.mintlify.app/cookbook/autoregressive/GLM/GLM-5.2)). Against
   vLLM's measured **2,200 tok/s/GPU decode** for a DeepSeek-R1-class MoE on H200, that is a
   **~2.7x gap** — partly because the cookbook measures a latency target (TTFT is already 6.1 s at
   concurrency 16) and partly real: the Dynamo recipe for GLM-5.3-Flash caps at **8,192 batched
   tokens and 32 max sequences**, which is a small batch.
2. **Prefill throughput for H200 is not published at all.** The vLLM blog gives GB200 prefill
   (26.2K tok/s/GPU) as a 3-5x improvement over H200, implying 5,240-8,730. This RFC uses
   **7,000 tok/s/GPU, or 56,000 tok/s per 8-GPU node, as a modelled estimate, not a measurement.**
   Every node count below scales linearly with it.
3. **Our own prefix-cache hit rate is unmeasured.** The 88.6% comes from a hosted provider's
   cache. Whether our own RadixAttention tree reproduces it depends on genuine prefix sharing
   across concurrent sessions, which nobody has measured. If it does not, prefill compute rises
   by up to 8.5x. **This is the assumption the entire economic case rests on.**
4. **Weight load and cold start are unmeasured end to end.** A measured EKS GPU autoscale
   reference gives 37 s to node-Ready and 84 s to pods-serving using a **130 MB stub image**, with
   image pull dominating ([codingwithtaz, 2026-05-13](https://codingwithtaz.blog/2026/05/13/production-ready-gpu-inference-autoscaling-on-eks-with-karpenter-keda-and-dragonfly/)).
   Real inference images are 8-40 GB, and the weights are a separate 306-331 GB (GLM) or ~330 GB
   (DeepSeek) transfer: at a generous 10 GB/s, **33 s of pure transfer before CUDA graph warmup**.
   No measured multi-hundred-GB weight-load time on AWS was found.
5. **Decode rate per user.** Hosted GLM-5.3-Flash measures ~43 output tok/s against DeepSeek
   V4-Flash's ~108 ([YottaLabs](https://www.yottalabs.ai/post/glm-5-3-flash-vs-deepseek-v4-flash-2026)).
   A self-hosted node has to beat the slow one, not match the fast one.
6. **Multimodal overhead.** Both models take image input, adding a vision tower and preprocessing
   to the hot path. Nothing here measures it.

## Burst and queueing

Continuous batching admits new requests into an in-flight batch until a memory or compute limit
binds; past that they queue, and TTFT rises roughly as queue depth divided by steady-state
throughput. The one measured curve available shows the shape: SGLang on GLM-5.2 at 8K-in goes
**TTFT 668 ms at concurrency 1 to 6,148 ms at concurrency 16** — a 9x degradation — while per-GPU
throughput rises only 4x
([SGLang cookbook](https://lmsysorg.mintlify.app/cookbook/autoregressive/GLM/GLM-5.2)).

Against that, our burst is **8.9x the mean hour and 15.7x the mean 5-minute** rate. An autoscaler
cannot help inside a burst: node-ready plus image pull is 84 s measured, and the weight transfer
adds tens more. A GPU node takes **minutes** to become useful, while the burst arrives in seconds
and the member's expectation is sub-second. Three consequences:

1. **Burst headroom must be pre-warmed**, meaning idle GPUs held for the peak — the largest cost
   line, and one no hosted price includes.
2. **Sizing to the mean is a deliberate bet that bursts queue.** On a chat surface that shows up
   as multi-second TTFT. It is a product decision, not an infrastructure one.
3. **The 09-10 spike is a one-off, not a tenant burst.** Model tenant burst separately.

A defensible middle: baseline pinned at the p95 hour, autoscale for the rest, and rate-limit by
queueing rather than degrading. It still leaves a several-minute hole at the front of a cold burst.

## Price comparison

### Hosted, at the measured mix

Using the repo's registered prices and the measured class mix, the blended hosted price per
million tokens, and the monthly cost of the measured 30-day volume (83.83e9 tokens):

| Model | Blended $/Mtok | Monthly, 30d volume |
|---|---|---|
| `z-ai/glm-5.3-flash` | $0.0471 | **$3,501** |
| `deepseek/deepseek-v4.1-flash` (off-peak, list) | $0.0242 | **$1,797** |
| `deepseek/deepseek-v4.1-flash` (peak) | $0.0458 | $3,594 |
| `gpt-5.6-luna` | $0.0504 | $3,741 |

That is the entire testing cluster. **The whole inference bill for the fleet we are sizing against
is $1,800-$3,600 a month.**

### Self-hosted, $/Mtok at utilization

One `p5en.48xlarge` (8xH200, public on-demand **$63.28/hr**, $7.91/GPU-hr
([Thunder Compute, Sep 2026](https://www.thundercompute.com/blog/nvidia-h200-pricing);
[Holori](https://calculator.holori.com/aws/ec2/p5en.48xlarge))); `p5e.48xlarge` is **$47.76/hr**
under EC2 Capacity Blocks ([AWS Capacity Blocks pricing](https://aws.amazon.com/ec2/capacityblocks/pricing/)).
Modelled node throughput: **56,000 t/s prefill, 17,600 t/s decode**.

| Utilization | prefill t/s | decode t/s | $/Mtok prefill | $/Mtok decode |
|---|---|---|---|---|
| 100% | 56,000 | 17,600 | $0.24 | $0.75 |
| 50% | 28,000 | 8,800 | $0.47 | $1.51 |
| 30% | 16,800 | 5,280 | $0.79 | $2.51 |
| 20% | 11,200 | 3,520 | $1.18 | $3.77 |
| 10% | 5,600 | 1,760 | $2.37 | $7.54 |
| **6.94%** (the fleet's actual mean) | 3,886 | 1,221 | **$3.41** | **$10.86** |

**The fleet's mean load occupies 6.94% of one 8-GPU H200 node.** At that utilization, self-hosted
prefill costs **$3.41/Mtok against a $0.0471/Mtok hosted blend — 72x more expensive.** Even at a
hypothetical 100% utilization, self-hosted prefill is **5.0x** the hosted blend, because the blend
is dominated by cache reads that cost $0.03/M hosted and, while cheap to serve, are not free to
*hold resident*.

A 3-year No-Upfront Compute Savings Plan at ~50% off takes $47.76/hr to ~$23.88/hr, halving every
figure. It changes 72x to 36x. It does not change the conclusion.

### Break-even volume

Solve for the volume at which one node's monthly cost equals the hosted value of what it serves, at
the measured mix:

| Target | Blended hosted $/Mtok | Break-even tokens/month for one node | Measured fleet (30d) | Shortfall |
|---|---|---|---|---|
| `z-ai/glm-5.3-flash` | $0.0471 | 740e9 | 83.8e9 | **8.8x** |
| `deepseek/deepseek-v4.1-flash` | $0.0242 | 1,440e9 | 83.8e9 | **17.2x** |

The GLM shortfall of 8.8x happens to equal the peak-hour-to-mean ratio exactly. Stated plainly:
**one H200 node pays for itself only when the workload runs continuously at its own busiest hour's
rate.** A tenant would have to make the fleet busier than its busiest hour, permanently.

### The small background model is the one close to break-even

One `g6e.2xlarge` (1xL40S 48 GB, 8 vCPU, 64 GiB): **$2.2421/hr on-demand, $1.4125/hr on a 1-year
reservation** ([InstanceFinder](https://instancefinder.com/aws/i/g6e.2xlarge.html)) — $1,031/mo
reserved — comfortably serves a Qwen3-4B or Qwen3-8B.

Background tier today at `gpt-5.6-luna` prices: 491.3e6 in + 146.2e6 cache read + 35.8e6 out per
7 days = **$144/week, $618/month**.

| Route | Monthly | vs hosted |
|---|---|---|
| Hosted, `gpt-5.6-luna` | $618 | — |
| Self-hosted, `g6e.2xlarge` 1-yr reserved | $1,031 | **1.67x worse** |
| Self-hosted, `g6e.2xlarge` on-demand | $1,637 | 2.65x worse |
| Self-hosted, `g6e.2xlarge` spot (us-east-2, ~$1.06/hr) | $774 | 1.25x worse |

**The only tier where the answer is close.** It is a single-GPU workload, the model is small, and
the load is steady rather than bursty because it is cron-driven, not person-driven. A 25% volume
increase, or spot with a warm fallback, or a cheaper small instance, crosses it. Worth a deliberate
decision rather than folding into the GPU-cluster question.

## Tenancy sizing

The brief treats this cluster as indicative of a **4-person tenant**. Taken literally, the implied
per-person rates are machine-heavy, not human: at 4 people the measured load is **1,112
turns/person/day** and **6.07M output tokens/person/day**. No human team does that. The cluster is
dominated by automated turn generation — `profile:coding` is 12% of turns and 54% of tokens, and
`profile:background` alone is 676.5e6 tokens/week.

The sizing is therefore presented with an explicit **multiplier** on the measured per-person rate,
because that assumption is the largest uncertainty here and every figure below scales linearly
with it.

Per-person basis at multiplier 1.0, from the 7-day mean rate:

| Measure | Per person |
|---|---|
| tokens/month | 18.57e9 |
| turns/day | 1,112 |
| prefill compute | 747.7 t/s |
| decode | 70.3 t/s |
| node-equivalents | 0.01735 |

### Capacity

Node-equivalents = prefill_tokens / 56,000 + decode_tokens / 17,600, at the modelled rates. Peak =
mean x 8.88.

| Tenant | Monthly tokens | Mean nodes | Peak-hour nodes |
|---|---|---|---|
| 20 people | 371e9 | 0.35 | 3.08 |
| 50 people | 928e9 | 0.87 | 7.70 |
| 100 people | 1,857e9 | 1.73 | 15.40 |

### Cost, self-hosted vs hosted

Nodes are 8xH200 `p5e.48xlarge` at the $47.76/hr capacity-block rate, $34,865/node-month. "SP" is
the 3-year No-Upfront Compute Savings Plan at ~50% off, $17,432/node-month. $mean sizes to
mean + N+1; $peak sizes to the peak hour, which is what a fleet with no queueing needs.

| Tenant | Hosted (GLM) | Mean+N+1 | vs hosted | Mean+N+1, SP | vs hosted | Peak-sized | vs hosted |
|---|---|---|---|---|---|---|---|
| 20 people | $17,503 | 2 nodes, $69,730 | **4.0x** | $34,865 | 2.0x | 4 nodes, $139,459 | 8.0x |
| 50 people | $43,758 | 2 nodes, $69,730 | **1.6x** | $34,865 | **0.80x** | 8 nodes, $278,918 | 6.4x |
| 100 people | $87,516 | 3 nodes, $104,594 | **1.2x** | $52,297 | **0.60x** | 16 nodes, $557,837 | 6.4x |

Against DeepSeek's hosted price the ratio is roughly 1.8x worse at every size, for the reason
above: half the hosted price, the same class of node.

The honest summary:

- **At 20 people, self-hosting is 4.0x the hosted bill** and no plausible discount closes it.
- **At 50 people it is 1.6x**, and only reaches ~0.8x on a three-year all-in commitment.
- **At 100 people, sized to the mean plus one, at list capacity-block rates, it is 1.2x** — still
  losing. With the Savings Plan it is 0.60x, a real win.
- **Every one of those "wins" buys queueing.** The mean-sized fleet has no burst headroom, and the
  peak-sized fleet that would have some costs 6.4x hosted. The gap between the two columns is the
  price of not queueing, and it is larger than the entire saving.

### Sensitivity

Four numbers move everything.

| Assumption | If pessimistic | Effect at 100 people, mean-sized |
|---|---|---|
| Prefill 56,000 t/s/node | 20,000 t/s/node (the GLM recipe's small batch) | node count x2.8 |
| Prefix-cache hit reproduces | does not; prefill x8.5 | node count x8.5 |
| Burst ratio 8.88x | tenants burst less, say 3x | peak column drops to ~2.2x hosted |
| Instance rate | on-demand $63.28/hr, not capacity block | +32% |

The second row is the dangerous one. The entire case rests on 88.6% of tokens being prefix-cache
hits our own tree can also hit. That has never been measured, and measuring it is cheap: replay one
real day of prompts through a single-node SGLang deployment and record the hit rate.

## Proposal

**Do not self-host the frontier tier. Do two smaller things.**

| Decision | Choice |
|---|---|
| GLM-5.3-Flash / DeepSeek-V4.1-Flash | **Stay hosted.** Self-hosting is 1.2x-4.0x more expensive at every tenant size the brief asks about, before the burst headroom a mean-sized fleet cannot provide. Revisit at a real 100-person tenant with a measured prefix-cache hit rate. |
| Small background model | **Run a measured pilot.** 1.25-1.67x away on a single L40S, steady cron-driven load, no burst problem, one GPU. |
| Serving stack for the pilot | SGLang with RadixAttention, one `g6e.2xlarge`, Qwen3-4B or Qwen3-8B, in the existing deploy shape rather than a new cluster. |
| Encoding | A new extension beside OpenRouter contributing `ModelSpec`s for the self-hosted endpoint through the existing `Manifest.models` point. **No core change**: `ModelPrice` already carries the units, and a self-hosted model's marginal price is a modelled $/Mtok that keeps the ledger honest. |
| Measure first | Replay one real day of prompts through a single-node deployment and record prefix-cache hit rate, prefill and decode tok/s/GPU, TTFT at p50/p95/p99 against concurrency, and node-ready-to-serving time. None of those five numbers exists today for these models on AWS. |

The pilot costs one `g6e.2xlarge`, $1,031-$1,637 a month. The decision it informs is a
$70k-$558k-a-month one. That ratio is the argument for doing it.

## Doctrine fit / implications

- **Core stays out of it.** A self-hosted model is a `ModelSpec` with a key slot and a base URL,
  exactly as OpenRouter is. `ModelPrice` and `price_digest` carry a modelled self-hosted rate
  through the ledger and the spend caps unchanged.
- **One shape.** The client is the existing OpenAI-wire client. Only the base URL moves.
- **Fail loud.** A self-hosted pool behind an autoscaler has one new failure mode: a request
  arriving before the node is warm. The registry must **not** silently fall back to a hosted model,
  because that fallback routes traffic and cost to a different provider without anyone deciding it.
  Fail the request and let the caller retry, or make the fallback an explicitly configured second
  `ModelSpec`.
- **What changes for the reader:** nothing yet. The deliverable is a decision not to build, plus
  one costed pilot.

## Alternatives

| Route | Why not |
|---|---|
| Self-host GLM-5.3-Flash now | 1.2x-4.0x the hosted bill at every size asked about, plus a burst problem a mean-sized fleet cannot absorb |
| Self-host DeepSeek-V4.1-Flash | worse on both sides: cheaper hosted, same class of node, 17.2x break-even shortfall |
| Serve both plus small models from one cluster | a single-node pilot answers the same questions for 1/50th the cost |
| Trainium / Inferentia | claimed 30-40% better price-performance than p5e/p5en, but Trainium2 capacity is reported "largely sold out" and vLLM/SGLang support is materially less mature than CUDA; unverified here |
| Bare-metal p5 | removes the nested-virtualization question at a much higher cost step, for a workload that does not need it |
| Keep everything hosted and stop analysing | the crossover is real and moves toward self-hosting with tenant size; the analysis is what makes it a decision instead of a surprise |

## Open decisions

1. **Do we believe the machine-heavy per-person rate?** Every capacity and cost number scales
   linearly with the multiplier. If a real 4-person tenant generates a quarter of the testing
   cluster's volume, break-even moves out 4x and self-hosting never wins.
2. **What burst behaviour will we accept?** Sizing to the mean means bursts queue for minutes;
   sizing to the peak costs 6.4x hosted. A product decision about the chat surface.
3. **Which small model for background jobs**, and does it clear the quality bar on memory
   extraction and titles that `gpt-5.6-luna` currently sets? The pilot should measure quality, not
   just throughput.
4. **Is a per-model container image an acceptable production dependency?** `glm5_next` is not in
   vLLM or SGLang `main`.
5. **What is the fallback when a self-hosted pool is cold?** Queue, fail, or route to a hosted
   model at a different price. A direct cost and a direct correctness consequence.

## Sources

Code: `core/src/ufo/harness/models/spec.py`, `pricing.py`, `catalog.py`,
`extensions/openrouter/ufo_ext_openrouter.py`, `infra/envs/prod/ufo.tf`,
`infra/envs/testing/ufo.tf`, `core/src/ufo/schema/tables.py`, `docs/usage-views.md`.

Models: [GLM-5.3-Flash deep dive](https://local-ai-zone.github.io/blog/glm-5-3-flash-deep-dive.html),
[vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3),
[NVIDIA Dynamo GLM-5.3-Flash recipe](https://docs.nvidia.com/dynamo/dev/recipes/glm-5-3-flash),
[community NVFP4 checkpoint card](https://huggingface.co/LibertAIDAI/GLM-5.3-Flash-NVFP4),
[DeepSeek-V4.1-Flash model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash),
[GLM vs DeepSeek self-hosting comparison, 2026-09-11](https://www.yottalabs.ai/post/glm-5-3-flash-vs-deepseek-v4-flash-2026),
[SGLang GLM-5.2 cookbook numbers](https://lmsysorg.mintlify.app/cookbook/autoregressive/GLM/GLM-5.2).

Serving: [vLLM wide-EP GB200/DeepSeek benchmark](https://vllm.ai/blog/2026-02-03-dsr1-gb200-part1),
[vLLM vs SGLang 2026](https://www.spheron.network/blog/vllm-vs-sglang-2026/),
[EKS GPU autoscaling cold-start measurements](https://codingwithtaz.blog/2026/05/13/production-ready-gpu-inference-autoscaling-on-eks-with-karpenter-keda-and-dragonfly/).

AWS: [p5en H200 pricing, Sep 2026](https://www.thundercompute.com/blog/nvidia-h200-pricing),
[EC2 Capacity Blocks pricing](https://aws.amazon.com/ec2/capacityblocks/pricing/),
[g6e.2xlarge](https://instancefinder.com/aws/i/g6e.2xlarge.html),
[EC2 on-demand pricing](https://aws.amazon.com/ec2/pricing/on-demand/),
[Savings Plans pricing](https://aws.amazon.com/savingsplans/compute-pricing/).

Hosted pricing: [DeepSeek API pricing](https://api-docs.deepseek.com/quick_start/pricing).

Sizing arithmetic is reproducible: `python3 /workspace/rfc2-maths.py`.
