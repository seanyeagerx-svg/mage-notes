"""Download only data files from a pinned public model revision; verify hashes."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent
REPO = "csukuangfj/sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30"
REV = "ad658fa0201659a09ea3c176129a191c77ecae8f"
FILES = {
    "encoder.int8.onnx": "5ac51e27981bb4dab01bb9be4958453ba50c3b61c063ddda0eab23fd3671aa4f",
    "decoder.onnx": "06522ad63cec0fdf6809f4e1db9bb4f7d710c34582e3b35db62ac60eccafac7e",
    "joiner.int8.onnx": "b34584dc6f561089e1d747fedebb3765f2caa72c927ef54d7ca55e5ae40a814b",
    "tokens.txt": "git:f15034ba47f26a74abdf68168b3e02c7055bb8b3",
    "README.md": "git:ba400b5371cb302ea54095d954895cae2b1cda6d",
}


def digest(data, expected):
    if expected.startswith("git:"):
        return "git:" + hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    return hashlib.sha256(data).hexdigest()


def main():
    dest = ROOT / "models" / "streaming-zh"
    records = []
    for name, expected in FILES.items():
        path = dest / name
        if not path.exists() or digest(path.read_bytes(), expected) != expected:
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f"https://huggingface.co/{REPO}/resolve/{REV}/{name}"
            print("Downloading", name, flush=True)
            with urllib.request.urlopen(url, timeout=90) as response:
                data = response.read()
            if digest(data, expected) != expected:
                raise RuntimeError(f"Checksum mismatch: {name}")
            part = path.with_suffix(path.suffix + ".part")
            part.write_bytes(data)
            part.replace(path)
        records.append({"file": name, "bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        print("Verified", name, path.stat().st_size, flush=True)
    (dest / "manifest.json").write_text(json.dumps(
        {"repository": REPO, "revision": REV, "files": records}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
