from evals.memory_ingestion.models import UpstreamAsset

LONGMEM_REVISION = "98d7416c24c778c2fee6e6f3006e7a073259d48f"
LOCOMO_REVISION = "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376"

LONGMEM_CLEANED = UpstreamAsset(
    name="longmem_cleaned",
    url=(
        "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/"
        f"{LONGMEM_REVISION}/longmemeval_s_cleaned.json"
    ),
    revision=LONGMEM_REVISION,
    size_bytes=277383467,
    sha256="sha256:d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442",
    license="MIT",
)
LOCOMO = UpstreamAsset(
    name="locomo10",
    url=(
        "https://raw.githubusercontent.com/snap-research/locomo/"
        f"{LOCOMO_REVISION}/data/locomo10.json"
    ),
    revision=LOCOMO_REVISION,
    size_bytes=2805274,
    sha256="sha256:79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4",
    license="CC BY-NC 4.0",
)

UPSTREAMS = (LONGMEM_CLEANED, LOCOMO)
