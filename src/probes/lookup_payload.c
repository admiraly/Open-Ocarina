/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Independently reconstructed bounded leaf probe; names/types are inferred. */
struct lookup_table { unsigned char *entries; unsigned int reserved; int stride; };
void *lookup_payload(const struct lookup_table *table, int index) {
    unsigned char *entry = *(unsigned char **)(table->entries + (long)table->stride * index);
    return *(void **)(entry + 8);
}
