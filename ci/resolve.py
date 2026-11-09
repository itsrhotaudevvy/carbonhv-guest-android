#!/usr/bin/env python3
"""Resolves which published AOSP build to download, without guessing silently.

Two ways in:
  * nothing passed  -> ask the public build API for the newest completed build and probe the
                       artifact names that Cuttlefish publishes for that product;
  * image_url given -> use it verbatim, no API call at all (this is the path to use when a human
                       downloaded the image in a browser and dropped it somewhere reachable).

Everything it decides goes to stdout as JSON and to $GITHUB_OUTPUT when running in Actions, so a
failed run leaves a record of which URLs were tried and what each of them answered.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# Google's legacy Build API answers `403 Rate limit exceeded for legacy API` to every unauthenticated
# client, from a Mac and from a GitHub runner alike. `status.json` is the public page the build
# dashboard itself reads, and it carries the last known good build id, which is all we need: the
# artifact host (ci.android.com/builds/submitted/...) serves bytes without an API key.
STATUS = "https://ci.android.com/builds/branches/{branch}/status.json"
SUBMITTED = "https://ci.android.com/builds/submitted/{build_id}/{target}/latest/{name}"
TIMEOUT = 60


def fail(code: int, message: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"resolve: {message}", file=sys.stderr)
    print(
        "resolve: if the build API refuses, dispatch this workflow with image_url and\n"
        "resolve: image_sha256 for a file downloaded by hand, or build from source in the\n"
        "resolve: carbonhv-guest-grapheneos repository.",
        file=sys.stderr,
    )
    raise SystemExit(code)


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "carbonhv-guest-android/1"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def head_ok(url: str) -> bool:
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "carbonhv-guest-android/1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        print(f"resolve: HEAD {url} -> {error.code}", file=sys.stderr)
        return False
    except urllib.error.URLError as error:
        print(f"resolve: HEAD {url} -> {error.reason}", file=sys.stderr)
        return False


def signed_url(viewer_url: str) -> str:
    """Turns a ci.android.com Artifact Viewer URL into the signed storage URL it embeds.

    Since 2025 the submitted-artifact path serves an HTML viewer, not bytes: a plain GET returns
    `text/html` with the file's signed Cloud Storage URL inside it. The signature is what makes the
    object readable without credentials, and it expires, so the URL is resolved and used in the
    same run rather than cached.
    """
    page = get(viewer_url).decode("utf-8", "replace")
    match = re.search(
        r"https://storage\.googleapis\.com/android-build/[^\"'\\\s]+(?:\\u0026[^\"'\\\s]+)*",
        page,
    )
    if not match:
        return ""
    return match.group(0).replace("\\u0026", "&")


def product_candidates(target: str) -> list[str]:
    """Artifact names are built from the product name, which is the target without its variant.

    `aosp_cf_arm64_only_phone-userdebug` publishes as either the full product name or the older
    `aosp_cf_arm64_phone`, depending on the branch, so both are tried and the log says which won.
    """
    base = target.rsplit("-", 1)[0]
    candidates = [base]
    if base.endswith("_only_phone"):
        candidates.append(base.replace("_only_phone", "_phone"))
    return candidates


def main(argv: list[str]) -> int:
    branch = argv[1] if len(argv) > 1 else "aosp-android-latest-release"
    target = argv[2] if len(argv) > 2 else "aosp_cf_arm64_only_phone-userdebug"
    image_url = argv[3] if len(argv) > 3 else ""
    image_sha256 = argv[4] if len(argv) > 4 else ""

    if image_url:
        result = {
            "source": "explicit-url",
            "image_url": image_url,
            "image_sha256": image_sha256,
            "host_package_url": "",
            "build_id": "",
            "branch": branch,
            "target": target,
        }
        return emit(result)

    try:
        status = json.loads(get(STATUS.format(branch=branch)))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")[:400]
        fail(3, f"status.json answered {error.code}: {body}")
    except urllib.error.URLError as error:
        fail(3, f"status.json unreachable: {error.reason}")

    published = [
        entry
        for entry in status.get("targets", [])
        if entry.get("name") == target or entry.get("ID", "").endswith(f".{target}")
    ]
    if not published:
        names = ", ".join(sorted(entry.get("name", "?") for entry in status.get("targets", [])))
        fail(4, f"branch {branch} does not publish target {target}; it publishes: {names}")
    entry = published[0]
    build_id = entry.get("last_known_good_build") or entry.get("first_known_good_build")
    if not build_id:
        fail(4, f"target {target} on {branch} has no known-good build listed")

    viewer_url = ""
    for product in product_candidates(target):
        candidate = SUBMITTED.format(
            build_id=build_id, target=target, name=f"{product}-img-{build_id}.zip"
        )
        if head_ok(candidate):
            viewer_url = candidate
            break
    if not viewer_url:
        fail(5, f"build {build_id}: none of the expected image artifact names answered 200")

    image_url = signed_url(viewer_url)
    if image_url:
        print(f"resolve: signed artifact url for build {build_id}", file=sys.stderr)
    else:
        print(
            "resolve: the artifact page carried no signed url; downloading the viewer page instead,\n"
            "resolve: which will fail the zip check below rather than being repacked as an image",
            file=sys.stderr,
        )
        image_url = viewer_url

    host_package = SUBMITTED.format(build_id=build_id, target=target, name="cvd-host_package.tar.gz")
    result = {
        "source": "status.json",
        "image_url": image_url,
        "image_sha256": image_sha256,
        "host_package_url": host_package if head_ok(host_package) else "",
        "build_id": build_id,
        "branch": branch,
        "target": target,
    }
    return emit(result)


def emit(result: dict) -> int:
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            for key, value in result.items():
                handle.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
