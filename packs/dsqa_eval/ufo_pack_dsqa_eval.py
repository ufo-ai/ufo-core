from ufo.sdk.manifest import Pack

VERSION = "0.1.0"
BASE_EXTENSIONS = ("index_default", "embed_openai", "openrouter")
CORE_NAME = "dsqa_core"
SEARCH_NAME = "dsqa_search"
BROWSER_NAME = "dsqa_browser"
CORE_EXTENSIONS = BASE_EXTENSIONS
SEARCH_EXTENSIONS = (*BASE_EXTENSIONS, "perplexity", "research")
BROWSER_EXTENSIONS = (*SEARCH_EXTENSIONS, "browser", "sandbox_chrome")


def core_pack() -> Pack:
    return Pack(name=CORE_NAME, version=VERSION, extensions=CORE_EXTENSIONS)


def search_pack() -> Pack:
    return Pack(name=SEARCH_NAME, version=VERSION, extensions=SEARCH_EXTENSIONS)


def browser_pack() -> Pack:
    return Pack(name=BROWSER_NAME, version=VERSION, extensions=BROWSER_EXTENSIONS)
