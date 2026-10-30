#!/usr/bin/env python3
"""Combine tested single-platform OCI archives into one multi-platform release layout per role.

Usage: oci-release-index.py <source-commit> <output-dir> <role>=<amd64.oci.tar>,<arm64.oci.tar> ...

Every blob is hash-checked while copied; each child config must name its expected platform.
The output index references exactly the tested child manifests, so a registry push of the
layout preserves their digests and the index digest recorded in release.json. Standard library
only; no network, registry or credentials.
"""
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
ARCHES = ("amd64", "arm64")
SOURCE = "https://github.com/portfall/plane"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def read_archive(path, arch, blobs):
    with tarfile.open(path) as archive:
        members = {m.name.removeprefix("./"): m for m in archive.getmembers() if m.isfile()}

        def blob(digest, size=None):
            algorithm, _, hexdigest = digest.partition(":")
            if algorithm != "sha256" or len(hexdigest) != 64:
                raise ValueError(f"{path}: unsupported digest {digest}")
            data = archive.extractfile(members[f"blobs/sha256/{hexdigest}"]).read()
            if hashlib.sha256(data).hexdigest() != hexdigest or (size is not None and len(data) != size):
                raise ValueError(f"{path}: blob {digest} does not match its descriptor")
            blobs[hexdigest] = data
            return data

        if json.loads(archive.extractfile(members["oci-layout"]).read()) != {"imageLayoutVersion": "1.0.0"}:
            raise ValueError(f"{path}: not an OCI image layout")
        top = json.loads(archive.extractfile(members["index.json"]).read())["manifests"]
        if len(top) != 1:
            raise ValueError(f"{path}: expected exactly one image, found {len(top)}")
        descriptor = top[0]
        if descriptor["mediaType"] == INDEX:
            nested = json.loads(blob(descriptor["digest"], descriptor["size"]))["manifests"]
            if len(nested) != 1:
                raise ValueError(f"{path}: attestation or extra manifests present; build with --provenance=false")
            descriptor = nested[0]
        if descriptor["mediaType"] != MANIFEST:
            raise ValueError(f"{path}: unexpected manifest type {descriptor['mediaType']}")
        manifest = json.loads(blob(descriptor["digest"], descriptor["size"]))
        config = json.loads(blob(manifest["config"]["digest"], manifest["config"]["size"]))
        if (config.get("os"), config.get("architecture")) != ("linux", arch):
            raise ValueError(f"{path}: config is {config.get('os')}/{config.get('architecture')}, not linux/{arch}")
        for layer in manifest["layers"]:
            blob(layer["digest"], layer["size"])
        return {
            "mediaType": MANIFEST, "digest": descriptor["digest"], "size": descriptor["size"],
            "platform": {"architecture": arch, "os": "linux"},
        }, manifest["config"]["digest"]


def write_layout(path, blobs, top):
    def add(archive, name, data):
        info = tarfile.TarInfo(name)
        info.size, info.mode, info.mtime = len(data), 0o644, 0
        archive.addfile(info, io.BytesIO(data))

    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
        add(archive, "oci-layout", canonical({"imageLayoutVersion": "1.0.0"}))
        add(archive, "index.json", canonical(top))
        for hexdigest in sorted(blobs):
            add(archive, f"blobs/sha256/{hexdigest}", blobs[hexdigest])


def main(argv):
    if len(argv) < 4:
        raise SystemExit(__doc__)
    commit, output = argv[1], Path(argv[2])
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise SystemExit("source commit must be a full lowercase SHA-1")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    release = {"schema": 1, "source": SOURCE, "source_commit": commit, "roles": {}}
    for spec in argv[3:]:
        role, _, paths = spec.partition("=")
        archives = paths.split(",")
        if not role or len(archives) != len(ARCHES):
            raise SystemExit(f"expected <role>=<amd64.tar>,<arm64.tar>, got {spec}")
        blobs, children, platforms = {}, [], {}
        for arch, archive in zip(ARCHES, archives):
            child, config = read_archive(archive, arch, blobs)
            children.append(child)
            platforms[arch] = {
                "manifest": child["digest"], "config": config,
                "archive_sha256": hashlib.sha256(Path(archive).read_bytes()).hexdigest(),
            }
        index = canonical({
            "schemaVersion": 2, "mediaType": INDEX, "manifests": children,
            "annotations": {"org.opencontainers.image.source": SOURCE, "org.opencontainers.image.revision": commit},
        })
        digest = hashlib.sha256(index).hexdigest()
        blobs[digest] = index
        layout = output / f"{role}.oci.tar"
        write_layout(layout, blobs, {"schemaVersion": 2, "mediaType": INDEX, "manifests": [{
            "mediaType": INDEX, "digest": "sha256:" + digest, "size": len(index),
            "annotations": {"org.opencontainers.image.ref.name": commit},
        }]})
        release["roles"][role] = {
            "index": "sha256:" + digest, "platforms": platforms,
            "layout_sha256": hashlib.sha256(layout.read_bytes()).hexdigest(),
        }
    (output / "release.json").write_text(json.dumps(release, indent=2, sort_keys=True) + "\n")
    with open(output / "index-digests.sha256", "w") as checksums:
        for role, value in sorted(release["roles"].items()):
            checksums.write(f"{value['index'].removeprefix('sha256:')}  internal/plane/{role}\n")


if __name__ == "__main__":
    main(sys.argv)
