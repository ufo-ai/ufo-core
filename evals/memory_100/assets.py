from evals.memory_100.models import UpstreamAsset

ENTERPRISE_REVISION = "56ba6a62cb66bf0a68ff995b1c423680980bf70a"
LONGMEM_REVISION = "98d7416c24c778c2fee6e6f3006e7a073259d48f"

ENTERPRISE_QUESTIONS = UpstreamAsset(
    name="enterprise_questions",
    url="https://github.com/onyx-dot-app/EnterpriseRAG-Bench/releases/download/v1.0.0/questions.jsonl",
    revision=ENTERPRISE_REVISION,
    size_bytes=764927,
    sha256="sha256:f9524b9157cd43aae36b99333a124738804306ea6d07f332d49faa6d3d147905",
    license="MIT",
)
ENTERPRISE_DOCUMENTS = UpstreamAsset(
    name="enterprise_documents",
    url="https://github.com/onyx-dot-app/EnterpriseRAG-Bench/releases/download/v1.0.0/all_documents.zip",
    revision=ENTERPRISE_REVISION,
    size_bytes=1256181062,
    sha256="sha256:9d1174928696ad08bc15f3f104739519de633c1605a4ec2034e0e3c0087bc5cd",
    license="MIT",
)
ENTERPRISE_OVERVIEW = UpstreamAsset(
    name="enterprise_overview",
    url=(
        "https://raw.githubusercontent.com/onyx-dot-app/EnterpriseRAG-Bench/"
        f"{ENTERPRISE_REVISION}/generated_data/company_overview.md"
    ),
    revision=ENTERPRISE_REVISION,
    size_bytes=7275,
    sha256="sha256:a889c45be24c8075c605e83771e704a437ccf60b33d4841f1dec7185333168d2",
    license="MIT",
)
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

UPSTREAMS = (ENTERPRISE_QUESTIONS, ENTERPRISE_DOCUMENTS, ENTERPRISE_OVERVIEW, LONGMEM_CLEANED)
