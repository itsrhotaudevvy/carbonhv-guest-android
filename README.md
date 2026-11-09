# carbonhv-guest-android

CI that turns an **official AOSP Cuttlefish ARM64 image** into a CarbonHV guest bundle.

This repository builds no Android source. It fetches a published, Google-built
`aosp_cf_arm64_only_phone-userdebug` image, records exactly which build it came from, verifies
what it can, repacks it into the CarbonHV artifact layout, and publishes the result with
provenance.

**It is not GrapheneOS and must never be labelled as GrapheneOS.** It is the stand-in guest the
CarbonHV plan sanctions while the real GrapheneOS-derived virtual-device image is built
(`docs/superpowers/plans/2026-11-08-qemu-emulated-engine.md`, "Phase 10 … use the AOSP CI
cuttlefish image as a clearly labelled stand-in guest. It is never shipped and never claimed as
GrapheneOS"). The real build lives in [carbonhv-guest-grapheneos](https://github.com/itsrhotaudevvy/carbonhv-guest-grapheneos).

## Why a separate repository

The image is a multi-gigabyte build artifact, not source. Keeping the fetch/verify/repack job out
of CarbonHV keeps that repository free of binary blobs and free of a workflow that depends on
Google's internal build API. CarbonHV consumes the published bundle by hash.

## What the workflow does

1. **Resolve** the newest completed build for the branch and target, through the public build API.
   If the API refuses (it rate-limits unauthenticated legacy clients), the job fails with the
   exact failure and the two ways forward: pass `image_url` + `image_sha256` explicitly, or build
   from source in the GrapheneOS-derived repository.
2. **Download** the image zip and `cvd-host_package.tar.gz`, then **verify** the checksum the API
   advertises. A mismatch is fatal.
3. **Repack**: unpack the image zip, keep `boot.img`, `init_boot.img`, `vendor_boot.img`,
   `super.img`, `userdata.img`, `vbmeta.img` and the partition metadata, and write a
   `bundle.toml` whose `artifact` lines carry the real SHA-256 of each file (the CarbonHV schema
   in `bundles/schema.md` requires real hashes before anything is signed or published).
4. **Publish** the bundle as a workflow artifact, and as a release asset when dispatched with a
   `release_tag`.

## Manual use

```
gh workflow run fetch-standin.yml -f branch=aosp-android-latest-release
gh workflow run fetch-standin.yml -f image_url=https://… -f image_sha256=…
```

The second form is what to use when the phone's owner has downloaded the image in a browser: the
workflow does not care where the bytes came from as long as the hash matches.
