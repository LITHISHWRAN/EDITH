import urllib.request
from pathlib import Path

DEST = Path("D:/EDITH/models/voice")
DEST.mkdir(parents=True, exist_ok=True)

FILES = {
    "kokoro-v1.0.onnx":
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    "voices-v1.0.bin":
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}

for name, url in FILES.items():
    target = DEST / name

    if target.exists() and target.stat().st_size > 1_000_000:
        print(f"  {name}: already present ({target.stat().st_size/1e6:.1f} MB)")
        continue

    print(f"  downloading {name} ...")
    urllib.request.urlretrieve(url, target)
    print(f"  {name}: {target.stat().st_size/1e6:.1f} MB")
