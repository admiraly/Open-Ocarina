# Sampled-image GPU input capture

`sampled-image-capture.patch` applies after the four capture patches and
`graphics-identity.patch`. It adds opt-in sampled-image metadata and GPU BEFORE
payloads for the exact compute program/variant/SPIR-V identity already selected
by [occurrence capture](occurrence-capture.md). It does not establish baseline
replay, rendering correctness or an FPS improvement.

The private target memory contract reports sampled-image reads at bindings 8–11
and no image writes for this exact emitted shader digest. This stage therefore
captures their inputs before the dispatch. It does not infer an image-output
contract for other shader identities.

## Enable and bound the capture

Use the existing identity/page-table/allocation settings, a fresh private capture
directory, and an explicitly selected occurrence. Then add:

```sh
export RX_VK_OO_IMAGES=1
export RX_VK_OO_IMAGE_BUDGET_MIB=256
```

The separate image staging cap defaults to 256 MiB and accepts decimal overrides
from 8 through 4096 MiB. All image staging is also charged against the existing
aggregate allocation budget: twice the candidate buffer bytes plus the image
staging bytes must fit. Budget failure creates explicit incomplete metadata and
no image payload copies. The 24 MiB page-table staging remains outside the
allocation budget, as in earlier stages. The image registry, view registry and
sampler registry have bounded live-entry counts of 65,536, 65,536 and 16,384;
missing owners remain explicit gaps. At most 64 submitted image descriptor rows
are retained; completeness requires exactly the four expected bindings.

Normal image/view/sampler creation parameters and original command order stay
unchanged. This opt-in introduces transfer operations and their barriers; use it
for evidence collection, never performance comparisons. All raw metadata,
addresses, hashes and payload artifacts belong in ignored private storage.

## Ownership and exact submitted state

The existing dispatch hook copies the actual `VkDescriptorImageInfo`, preserving
binding, array element, descriptor type, image view, sampler and submitted layout.
A view registry copies the actual successful `VkImageViewCreateInfo` from the
renderer’s texture creation path, including component swizzles, format, view type,
subresource range and known `VkImageViewUsageCreateInfo`. A VMA registry records
actual image creation facts, exact extent/mips/layers/samples/usage/flags, memory
identity/binding offset, sharing/queue-family metadata and known format-list
extensions. Image allocation padding is never interpreted as texel payload size.
Unknown owners and unsupported extension chains remain incomplete; known view
metadata is preserved even when its image owner is missing.

The normal game sampler creation path records all 16 core creation words, plus
ordered reduction-mode and custom-border-color extensions. Float parameters and
border-color union values retain exact unsigned 32-bit representations. The
`core_words` order is:

```text
flags, magFilter, minFilter, mipmapMode, addressModeU, addressModeV,
addressModeW, mipLodBias_bits, anisotropyEnable, maxAnisotropy_bits,
compareEnable, compareOp, minLod_bits, maxLod_bits, borderColor,
unnormalizedCoordinates
```

Known sampler extension records contain one reduction-mode word, or four border
color words followed by the format word. Unknown extension types are recorded as
unsupported rather than guessed. Samplers committed to the existing cache live
for the process; failed uncommitted samplers are removed from the registry before
destruction. Auxiliary samplers/views created outside the instrumented texture
paths are unresolved if they appear in selected descriptors.

Every resolved source image is pinned before budget/allocation decisions. A
requested VMA image destruction waits for that pin’s exact capture completion or
safe cancellation release. Metadata-only failures release the same pins. Multiple
views retain the same original image identity, and the checker rejects conflicting
creation facts for one handle. Underlying physical aliasing between distinct
images remains unproven.

## Supported payloads and synchronization

The initial transfer path supports known color formats with one sample and
nonsparse, nonprotected 1D/2D images, including array and cube views. It accepts
GENERAL submitted layouts. It copies every mip and layer selected by each view,
in the **backing image format**, preserving view format/swizzles separately.
Supported format families are R8/RG8, RGBA8/BGRA8 and related 32-bit packed formats,
R16/RG16/RGBA16, R32/RG32/RGBA32, packed formats 122/123, and BC1–BC7. The helper
contains the exact enum ranges and block sizes. Unknown formats, noncolor aspects,
MSAA, 3D/sliced images, unsupported flags/extensions and inconsistent view usage
produce explicit gaps. No image flags or usages are expanded to force support.

Footprints use complete backing-format texel blocks, selected mip dimensions and
selected layer count. Arithmetic and mip/layer bounds are checked; short
compressed edges consume their complete final block. Each mip is tightly packed
with `bufferRowLength=bufferImageHeight=0`, aligned to `max(4, blockBytes)` within
the staging artifact. Alignment/tail padding is filled with zero by the GPU.
There is no attempt to read opaque image allocation bytes or reconstruct an image
from CPU assets. See the official
[Vulkan image-copy guide](https://docs.vulkan.org/guide/latest/image_copies.html)
and [image-to-buffer copy requirements](https://docs.vulkan.org/refpages/latest/refpages/source/vkCmdCopyImageToBuffer.html).

Barriers order prior GPU writes before transfer reads, transition only the
selected image subresources from the submitted GENERAL layout to TRANSFER_SRC,
and restore that exact layout before the selected compute dispatch. Buffer
barriers order the zero fill before copy writes and expose completed transfer
writes to the host. Copies for overlapping views are sequential and retain their
source identity. Conflicting descriptor layouts for one image cause an explicit
capture failure rather than speculative transitions.

Payload export waits for the same exact recording sequence used by the buffer and
page-table stages. Staging is invalidated before export. `image-N-before.bin` is
hashed and referenced by the manifest committed last. A partial capture may still
contain supported image payloads, with unsupported rows explicitly missing. A
binary without its committed manifest is incomplete evidence.

## Schema and remaining replay gaps

`images.json` uses `open-ocarina.sampled-images/v1`. It records the selected
occurrence, full target identity and exact recording completion, then descriptor
rows with nested `image`, `view`, `sampler`, `copy` and `before` objects. The
`copy.mips` entries identify level, extent, byte offset and byte count; array
layers are packed in ascending order from `view.base_layer`. `before` supplies
the artifact name and SHA-256. This is normalized GPU transfer data, not a dump of
Vulkan’s opaque image memory layout.

`image_inputs_complete` describes this sampled-image component only. It requires
all four expected descriptor inputs, supported exact metadata and complete
payloads. `replay_complete` always remains false. Complete push constants,
submitted descriptor-word decoding, image/import alias closure, CPU writer
quiescence and temporal-state coverage remain explicitly false. Existing v2
buffer manifests retain their earlier coverage fields; a future replay assembler
must combine independently checked components and resolve their remaining gaps.

## Validation

```sh
python3 patches/hookshot/check_sampled_image_capture.py --checkout local/hookshot-source
python3 -m unittest discover -s tests -p test_sampled_image_capture.py -v
python3 patches/hookshot/check_sampled_image_capture.py --validate-snapshot local/private-capture
```

The checker validates the six-patch chain with an alternate index, compiles an
optimized standalone helper from public patch text, and verifies a synthetic
capture with multiple views of one image, selected mips/layers and defined
padding. It rejects premature completion, altered footprints, unsupported
creation claims, missing sampler words/extensions, conflicting source metadata,
budget omissions, corrupted/truncated payloads, symlinks and escaping paths.
The snapshot command also checks the linked v2 buffer/table capture and matching
identity, timeline and occurrence. These checks execute no GPU workload and do
not independently attest producer GPU execution.

## Isolated runtime observation

The [combined image/parameter observation](../research/gpu-image-dispatch-observation.json)
pins the seven-patch renderer and existing isolated host. The selected dispatch
exported all four supported sampled inputs, including the array image, together
with exact sampler/view metadata. The independent checker verified the image
payloads and linked buffer/table capture. A separate parameter check verified the
88 pushed bytes, actual descriptor layout, direct grid and subgroup request from
the same recording. The process stopped cleanly.

These are joined component checks. Original image-manifest coverage declarations
retain their scope; backing aliases, writer atomicity, complete device/temporal
state and an independent shader-output comparison remain open. Full replay and
performance are not established by this capture.
