#include <assert.h>
#include <stdlib.h>
#include "src/cid/cidload.c"

static void *allocate(FT_Memory memory, long size) {
    (void) memory;
    return malloc(size);
}

static void release(FT_Memory memory, void *block) {
    (void) memory;
    free(block);
}

static void *resize(FT_Memory memory, long old_size, long size, void *block) {
    (void) memory;
    (void) old_size;
    return realloc(block, size);
}

static void check(unsigned count, int reject) {
    struct FT_MemoryRec_ memory = {NULL, allocate, release, resize};
    FT_Byte data[16] = {2, 16};
    FT_StreamRec stream = {0};
    CID_FaceRec face = {0};
    CID_FaceDictRec dictionaries[6] = {0};
    stream.memory = &memory;
    FT_Stream_OpenMemory(&stream, data, sizeof(data));
    face.root.memory = &memory;
    face.cid_stream = &stream;
    face.cid.num_dicts = count;
    face.cid.font_dicts = dictionaries;
    for (unsigned i = 0; i < count; i++) {
        dictionaries[i].num_subrs = 1;
        dictionaries[i].sd_bytes = 1;
        dictionaries[i].private_dict.lenIV = -1;
    }
    FT_Error error = cid_read_subrs(&face);
    if (reject) {
        assert(error != 0);
        assert(face.subrs == NULL);
        return;
    }
    assert(error == 0);
    for (unsigned i = 0; i < count; i++) {
        assert(face.subrs[i].num_subrs == 1);
        assert(face.subrs[i].code[1] - face.subrs[i].code[0] == 14);
        free(face.subrs[i].code[0]);
        free(face.subrs[i].code);
    }
    free(face.subrs);
}

int main(void) {
    check(1, 0);
    check(4, 0);
    check(6, 1);
    return 0;
}
