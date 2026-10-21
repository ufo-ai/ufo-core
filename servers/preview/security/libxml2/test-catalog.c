#include <assert.h>
#include <string.h>
#include "catalog.c"

int main(void) {
    const char *content =
        "<catalog xmlns='urn:oasis:names:tc:entity:xmlns:xml:catalog'>"
        "<nextCatalog catalog='valid.xml'/><nextCatalog/>"
        "<system systemId='urn:ufo:catalog' uri='normal.xml'/>"
        "</catalog>";
    xmlDocPtr document = xmlReadMemory(content, strlen(content), "catalog.xml", NULL,
                                       XML_PARSE_NONET);
    assert(document != NULL);
    xmlCatalogEntryPtr parent = xmlNewCatalogEntry(XML_CATA_CATALOG, NULL, NULL,
                                                   NULL, XML_CATA_PREFER_NONE, NULL);
    assert(parent != NULL);
    xmlParseXMLCatalogNodeList(xmlDocGetRootElement(document)->children,
                              XML_CATA_PREFER_NONE, parent, NULL);
    assert(parent->children != NULL);
    assert(parent->children->type == XML_CATA_NEXT_CATALOG);
    assert(xmlStrEqual(parent->children->URL, BAD_CAST "valid.xml"));
    assert(parent->children->next != NULL);
    assert(parent->children->next->type == XML_CATA_SYSTEM);
    assert(parent->children->next->next == NULL);
    xmlFreeCatalogEntryList(parent);
    xmlFreeDoc(document);
    xmlCleanupParser();
    return 0;
}
