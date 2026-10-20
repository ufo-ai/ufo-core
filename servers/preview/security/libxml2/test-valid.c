#include <assert.h>
#include "valid.c"

int main(void) {
    xmlNode node = {0};
    char buffer[16] = "abcdef";
    node.type = XML_COMMENT_NODE;
    memset(buffer + 7, '!', 9);
    xmlSnprintfElements(buffer, 7, &node, 1);
    assert(strcmp(buffer, "abcdef") == 0);
    assert(buffer[7] == '!');
    strcpy(buffer, "");
    xmlSnprintfElements(buffer, sizeof(buffer), &node, 1);
    assert(strcmp(buffer, " ...") == 0);
    return 0;
}
