#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>
#include "gzguts.h"

int main(void) {
    unsigned char payload[65536];
    unsigned value = 0x12345678U;
    for (unsigned i = 0; i < sizeof(payload); ++i) {
        value ^= value << 13;
        value ^= value >> 17;
        value ^= value << 5;
        payload[i] = (unsigned char)value;
    }
    const char *modes[] = {"wT", "wb"};
    for (unsigned i = 0; i < 2; ++i) {
        int fd = open("/dev/full", O_WRONLY);
        if (fd < 0) return 1;
        gzFile file = gzdopen(fd, modes[i]);
        if (file == NULL || gzbuffer(file, 32) != 0) return 2;
        if (gzwrite(file, payload, sizeof(payload)) != 0) return 3;
        int error = Z_OK;
        gzerror(file, &error);
        if (error != Z_ERRNO) return 4;
        gz_statep state = (gz_statep)file;
        if (state->strm.avail_in != 0 || state->strm.next_in != state->in) {
            fprintf(stderr, "retained caller input after write error: %s\n", modes[i]);
            return 42;
        }
        (void)gzclose(file);
    }
    puts("zlib write-error state reset passed for direct and compressed writes");
    return 0;
}
