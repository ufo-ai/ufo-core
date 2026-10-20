#define _GNU_SOURCE
#include <assert.h>
#include <sys/mman.h>
#include "xmlIO.c"

static int calls;

static int write_output(void *context, const char *data, int length) {
    (void) context;
    (void) data;
    calls++;
    assert(length >= 0);
    return length;
}

int main(void) {
    const size_t size = (size_t) INT_MAX + 1;
    void *content = mmap(NULL, size, PROT_READ, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(content != MAP_FAILED);
    xmlBufferPtr raw = xmlBufferCreateStatic(content, size);
    xmlOutputBufferPtr output = xmlOutputBufferCreateIO(write_output, NULL, NULL, NULL);
    assert(raw != NULL && output != NULL);
    xmlBufFree(output->buffer);
    output->buffer = xmlBufFromBuffer(raw);
    assert(xmlOutputBufferFlush(output) == -1);
    assert(output->error == XML_ERR_INTERNAL_ERROR);
    assert(calls == 0);
    xmlOutputBufferClose(output);
    xmlBufferFree(raw);
    assert(munmap(content, size) == 0);
    return 0;
}
