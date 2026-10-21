#include <assert.h>
#include <ft2build.h>
#include FT_FREETYPE_H

int main(void) {
    FT_Library library;
    FT_Face face;
    assert(FT_Init_FreeType(&library) == 0);
    assert(FT_New_Face(library, "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0, &face) == 0);
    assert(FT_Set_Pixel_Sizes(face, 0, 24) == 0);
    assert(FT_Load_Char(face, 'A', FT_LOAD_RENDER) == 0);
    assert(face->glyph->bitmap.width > 0 && face->glyph->bitmap.rows > 0);
    FT_Done_Face(face);
    FT_Done_FreeType(library);
    return 0;
}
