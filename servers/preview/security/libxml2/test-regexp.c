#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include "xmlregexp.c"

static unsigned allocations;
static int reject_string;

static void *allocate(size_t size) {
    allocations++;
    if (reject_string && allocations == 2)
        return NULL;
    return malloc(size);
}

static char *copy(const char *value) {
    size_t size = strlen(value) + 1;
    char *result = allocate(size);
    if (result != NULL)
        memcpy(result, value, size);
    return result;
}

int main(void) {
    assert(xmlMemSetup(free, allocate, realloc, copy) == 0);
    allocations = 0;
    reject_string = 1;
    assert(xmlRegNewParserCtxt(BAD_CAST "abc") == NULL);
    assert(allocations >= 2);
    reject_string = 0;
    xmlRegexpPtr expression = xmlRegexpCompile(BAD_CAST "[a-z]+");
    assert(expression != NULL);
    assert(xmlRegexpExec(expression, BAD_CAST "abc") == 1);
    xmlRegFreeRegexp(expression);
    xmlCleanupParser();
    return 0;
}
