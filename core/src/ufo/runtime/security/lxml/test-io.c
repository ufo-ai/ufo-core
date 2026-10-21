#include <assert.h>
#include "buf.c"
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
    xmlOutputBufferPtr output = xmlOutputBufferCreateIO(write_output, NULL, NULL, NULL);
    assert(output != NULL && output->buffer != NULL);
    output->buffer->use = (size_t) INT_MAX + 1;
    UPDATE_COMPAT(output->buffer)
    assert(xmlOutputBufferFlush(output) == -1);
    assert(output->error == XML_ERR_INTERNAL_ERROR);
    assert(calls == 0);
    xmlOutputBufferClose(output);
    return 0;
}
