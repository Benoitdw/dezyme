#!/bin/sh
# Start the PoPMuSiC container: make sure the SaProt weights are in /models,
# then serve the wrapper.
set -e

# popmusicevol-install downloads the weights with force_download=True, so a
# restart during the first download (~3 GB) would start over. Fetch them here
# first: this resumes where it stopped and is a no-op once they are complete.
python - <<'EOF'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="westlake-repl/SaProt_650M_PDB",
    local_dir="/models/saprot",
    ignore_patterns=["*.msgpack", "*.h5", "*.tflite", "*.safetensors"],
)
EOF

# Only checks MuSiC, SaProt and Foldseek now that everything is in place.
popmusicevol-install

exec /opt/wrapper/bin/uvicorn api:app --host 0.0.0.0 --port 8000
