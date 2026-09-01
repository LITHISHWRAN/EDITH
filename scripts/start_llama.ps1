# Starts the local model server EDITH talks to.
#
# -c 16384          : ~2900 of those tokens are tool schemas on every turn,
#                     so 8192 left very little room for search results.
# -fa on            : flash attention, required for a quantised KV cache.
# --cache-type-* q8_0: halves the KV cache, which is what makes 16k fit in
#                     8 GB of VRAM alongside the model.
#
# Keep -c and LLAMA_CONTEXT in .env in step: EDITH derives its history
# budget from that number.

param(
    [int]$Context = 16384,
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$server = Join-Path $root "runtime\llama.cpp\llama-server.exe"
$model = Join-Path $root "models\assistant.gguf"

if (-not (Test-Path $server)) { throw "llama-server not found at $server" }
if (-not (Test-Path $model))  { throw "model not found at $model" }

Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

& $server `
    -m $model `
    --host 127.0.0.1 --port $Port `
    -c $Context `
    -ngl 99 `
    -fa on `
    --cache-type-k q8_0 --cache-type-v q8_0
