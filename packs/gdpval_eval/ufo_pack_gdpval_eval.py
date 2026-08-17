from ufo.sdk.manifest import Pack

VERSION = "0.1.0"
BASE_EXTENSIONS = ("index_default", "embed_openai", "openrouter")
DOCUMENT_EXTENSIONS = ("documents", "repl", "coding")
RESEARCH_EXTENSIONS = ("perplexity", "research", "browser", "sandbox_chrome")
CORE_NAME = "gdpval_core"
DOCUMENTS_NAME = "gdpval_documents"
RESEARCH_NAME = "gdpval_research"
FULL_NAME = "gdpval_full"


def core_pack() -> Pack:
    return Pack(name=CORE_NAME, version=VERSION, extensions=BASE_EXTENSIONS)


def documents_pack() -> Pack:
    return Pack(
        name=DOCUMENTS_NAME,
        version=VERSION,
        extensions=(*BASE_EXTENSIONS, *DOCUMENT_EXTENSIONS),
    )


def research_pack() -> Pack:
    return Pack(
        name=RESEARCH_NAME,
        version=VERSION,
        extensions=(*BASE_EXTENSIONS, *RESEARCH_EXTENSIONS),
    )


def full_pack() -> Pack:
    return Pack(
        name=FULL_NAME,
        version=VERSION,
        extensions=(*BASE_EXTENSIONS, *DOCUMENT_EXTENSIONS, *RESEARCH_EXTENSIONS),
    )
