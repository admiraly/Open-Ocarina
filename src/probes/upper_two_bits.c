/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Independently reconstructed bounded leaf probe. */
struct flags_object { unsigned char reserved[24]; unsigned int flags; };
unsigned int upper_two_bits(const struct flags_object *object) {
    return object->flags >> 30;
}
