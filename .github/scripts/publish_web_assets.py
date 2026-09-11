#!/usr/bin/env python3

"""Write this build's portal asset tree into a deploy's blob bucket, before its pods roll.

A pod publishes its own assets at boot (RFC 0031), and until that publish lands the pages and
reads that await it wait on up to 160 objects. Running the same publish here, from the tree the
image is built from, means every key is already present when the new pods start: their own publish
costs the two listings that find nothing to write. The surface owns the keys and the digest, so
this reads them from the surface rather than restating them — a prefix that moves here and there
would publish a tree no page names.
"""

from __future__ import annotations

import argparse
import asyncio

from ufo_ext_web.surface import APPS_STORE_PREFIX, STATIC_ASSETS, apps, publish_assets

from ufo.blob import FleetBlobStore, S3BlobStore


async def publish(bucket: str) -> None:
    backend = S3BlobStore(bucket=bucket)
    bundle = apps()
    try:
        await publish_assets(FleetBlobStore(backend=backend), bundle)
    finally:
        await backend.close()
    print(
        f"published {len(STATIC_ASSETS)} static assets and {len(bundle.files)} app files "
        f"under {APPS_STORE_PREFIX}{bundle.digest}/ to {bucket}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True, help="The environment's blob bucket.")
    asyncio.run(publish(parser.parse_args().bucket))


if __name__ == "__main__":
    main()
