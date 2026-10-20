#include <assert.h>
#include "dict.c"

static size_t requested;

static void *deny_allocation(size_t size) {
    requested = size;
    return NULL;
}

int main(void) {
    xmlDict dictionary = {0};
    xmlMallocFunc previous = xmlMalloc;
    xmlMalloc = deny_allocation;
    assert(xmlDictAddQString(&dictionary, BAD_CAST "p", UINT_MAX / 4 + 100,
                            BAD_CAST "n", 1) == NULL);
    xmlMalloc = previous;
    assert(requested > UINT_MAX);
    return 0;
}
