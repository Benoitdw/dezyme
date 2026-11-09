# PoPMuSiC-evol + the generic HTTP wrapper.
#
# Build contexts (see docker-compose.yml):
#   app     -> softwares/popmusic (git submodule, PoPMuSiCv3 repo, not modified)
#   wrapper -> wrapper/
#
# torch is installed from PyPI with its CUDA libraries: the tool uses the GPU
# when the container is given one and falls back to the CPU otherwise.
# SaProt weights (~3 GB) are not in the image: they are downloaded on first
# start into /models, bind-mounted from ./models/popmusic.

FROM python:3.10-slim

ARG UID=1000
ARG GID=1000

RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc g++ libc6-dev wget ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Wrapper in its own venv so its dependencies never touch PoPMuSiC's.
RUN python -m venv /opt/wrapper \
 && /opt/wrapper/bin/pip install --no-cache-dir fastapi "uvicorn[standard]"

WORKDIR /src

# 1. Dependencies only (torch, transformers...): this heavy layer is rebuilt
#    only when pyproject.toml changes, not on every code change.
COPY --from=app pyproject.toml README.md ./
RUN mkdir popmusicevol && touch popmusicevol/__init__.py \
 && pip install --no-cache-dir . \
 && pip uninstall -y popmusicevol \
 && rm -rf /src/*

# 2. The package itself.
COPY --from=app . .
RUN pip install --no-cache-dir --no-deps . && rm -rf /src

# 3. Compile MuSiC and fetch Foldseek into the package, then point the SaProt
#    weights directory to /models so it can live outside the image.
RUN python - <<'EOF'
from popmusicevol.predictors import PoPMuSiCPredictor, SaProtPredictor
PoPMuSiCPredictor.verify_executable()
SaProtPredictor.verify_foldseek_executable()
EOF
# The MuSiC build leaves its binaries 0710 root: make the whole package
# readable and runnable by the container user, who must not need to rebuild.
RUN chmod -R a+rX "$(python -c 'import popmusicevol, os; print(os.path.dirname(popmusicevol.__file__))')"
RUN set -eux; \
    weights="$(python -c 'from popmusicevol.predictors import SaProtPredictor as S; print(S.saprot_model_path)')"; \
    mkdir -p /models/saprot "$(dirname "$weights")"; \
    rm -rf "$weights"; \
    ln -s /models/saprot "$weights"; \
    chown -R ${UID}:${GID} /models "$(dirname "$weights")"

COPY --from=wrapper api.py /wrapper/api.py
COPY popmusic-start.sh /usr/local/bin/popmusic-start

# PYTHONUNBUFFERED: stdout reaches job.log as it is printed, not in 8 KB blocks.
ENV HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/tmp/huggingface \
    RUN_CMD=popmusicevol \
    JOBS_DIR=/data/jobs

USER ${UID}:${GID}
WORKDIR /wrapper
EXPOSE 8000

# Downloads the SaProt weights into /models on the first start (resumable),
# checks the install, then serves the wrapper.
CMD ["sh", "/usr/local/bin/popmusic-start"]
