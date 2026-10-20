#include <assert.h>
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
    char content = 0;
    xmlBufferPtr raw = xmlBufferCreateStatic(&content, (size_t) INT_MAX + 1);
    xmlOutputBufferPtr output = xmlOutputBufferCreateIO(write_output, NULL, NULL, NULL);
    assert(raw != NULL && output != NULL);
    xmlBufFree(output->buffer);
    output->buffer = xmlBufFromBuffer(raw);
    assert(xmlOutputBufferFlush(output) == -1);
    assert(output->error == XML_ERR_INTERNAL_ERROR);
    assert(calls == 0);
    xmlOutputBufferClose(output);
    xmlBufferFree(raw);
    return 0;
}
