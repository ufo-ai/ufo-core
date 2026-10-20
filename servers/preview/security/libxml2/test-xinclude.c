#include <assert.h>
#include "xinclude.c"

static int calls;
static int options;

static xmlParserInputPtr load_resource(const char *url, const char *id,
                                      xmlParserCtxtPtr context) {
    (void) url;
    (void) id;
    calls++;
    options = context->options;
    return NULL;
}

int main(void) {
    const char *input = "<doc xmlns:xi='http://www.w3.org/2001/XInclude'>"
        "<xi:include href='http://example.invalid/test' parse='%s'>"
        "<xi:fallback>blocked</xi:fallback></xi:include></doc>";
    const char *kinds[] = {"xml", "text"};
    for (size_t kind = 0; kind < 2; kind++) {
        for (int tree = 0; tree < 2; tree++) {
            char document[512];
            int length = snprintf(document, sizeof(document), input, kinds[kind]);
            xmlDocPtr doc = xmlReadMemory(document, length, "input.xml", NULL, XML_PARSE_NONET);
            assert(doc != NULL);
            calls = 0;
            options = 0;
            xmlExternalEntityLoader previous = xmlGetExternalEntityLoader();
            xmlSetExternalEntityLoader(load_resource);
            int result = tree ? xmlXIncludeProcessTree(xmlDocGetRootElement(doc))
                              : xmlXIncludeProcess(doc);
            xmlSetExternalEntityLoader(previous);
            assert(result >= 0);
            assert(calls == 1);
            assert(options & XML_PARSE_NONET);
            xmlFreeDoc(doc);
        }
    }
    return 0;
}
