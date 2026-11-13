# Original-path native-network metadata

One bounded A770 run now supplies real compiler queries and a registered
texture for movie-network stage `e409733c45530d75`. The stage still fails at
the existing network exclusion before SPIR-V emission. No native-network
restoration or GPU execution of this stage is claimed.

The [sanitized observation](../research/ndve-live-metadata-observation.json)
pins the observer, optimized renderer, host, driver, runtime plan and private
journal. The parent verified the mapped renderer/driver path and inode,
stopped and reaped its owned process, and rechecked the source/binary pins and
common save/cache seed. All 207 renderer compilation units built successfully,
with at most four compiler jobs. The source observer's ten CPU checks, 34
supervisor checks and five parent prelaunch checks passed.

The journal contains four complete contexts and 47 actual compiler queries.
The translation used the bound program, with neither forced nor replayed
answers. Its c0 byte-88 query returned the real texture handle; the integer
query returned false. The later dispatch sample matched that driver word to
the native texture-table slot and a registered 1920×1080 RGBA8 texture. Its
registration generation was unchanged around the sample.

All 21 actual `HostTranslateInfo` fields were observed at their original use.
The later sampled host configuration agrees with those values. No actual
SPIR-V profile consumption occurred: the exclusion happens first. The 77-field
profile sampled at dispatch remains explicitly a sample for a future export.
The renderer's `dev.native_mma` flag and software matrix mode were both true;
that policy flag does not establish execution of hardware matrix instructions.

This replaces assumptions about one texture classification and the host
configuration. The [earlier conditional export](ndve-stage-software-export.md)
remains a different offline artifact with synthetic handle tokens and an
assumed profile. A new export must use the observed configuration and retain
the distinction between actual compiler use and later sampling. Complete
buffer/image inputs, payload atomicity, shader arithmetic, native feature
restoration and gameplay performance remain open.
