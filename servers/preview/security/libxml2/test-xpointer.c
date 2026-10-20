#define _GNU_SOURCE
#include <assert.h>
#include <sys/mman.h>
#include <unistd.h>
#include "xpointer.c"

int main(void) {
    const size_t unit = 1024 * 1024;
    const size_t size = ((size_t) INT_MAX / unit + 2) * unit;
    FILE *file = tmpfile();
    assert(file != NULL);
    assert(ftruncate(fileno(file), unit) == 0);
    char *seed = mmap(NULL, unit, PROT_READ | PROT_WRITE, MAP_SHARED, fileno(file), 0);
    assert(seed != MAP_FAILED);
    memset(seed, 'x', unit);
    assert(munmap(seed, unit) == 0);
    char *input = mmap(NULL, size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(input != MAP_FAILED);
    for (size_t offset = 0; offset < size; offset += unit)
        assert(mmap(input + offset, unit, PROT_READ | PROT_WRITE,
                    MAP_PRIVATE | MAP_FIXED, fileno(file), 0) == input + offset);
    input[0] = '(';
    input[size - 1] = 0;
    xmlDocPtr doc = xmlNewDoc(BAD_CAST "1.0");
    xmlXPathContextPtr context = xmlXPtrNewContext(doc, NULL, NULL);
    xmlXPathParserContextPtr parser = xmlXPathNewParserContext(BAD_CAST input, context);
    assert(parser != NULL);
    assert(xmlStrlen(BAD_CAST input + 1) == 0);
    assert(xmlURIEscapeStr(BAD_CAST input + 1, NULL) == NULL);
    xmlXPtrEvalXPtrPart(parser, xmlStrdup(BAD_CAST "xpointer"));
    assert(parser->cur == BAD_CAST input + 1);
    xmlXPathFreeParserContext(parser);
    xmlXPathFreeContext(context);
    xmlFreeDoc(doc);
    assert(munmap(input, size) == 0);
    fclose(file);
    return 0;
}
