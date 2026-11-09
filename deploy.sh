#!/bin/sh
# Update the code and (re)start the stack. Run from anywhere, as the deploy user.
#
# backend/ and frontend/ are checked out at the commits recorded in this repo;
# softwares/popmusic always follows the latest commit of its main branch.
# Rebuilding popmusic kills a running analysis (job_dead): deploy when idle.
set -e
cd "$(dirname "$0")"

git pull
git submodule sync --recursive
git submodule update --init --recursive
git submodule update --init --remote softwares/popmusic
echo "PoPMuSiC at $(git -C softwares/popmusic log -1 --format='%h %ad %s' --date=short)"

# Created here, as the deploy user: if Docker created them they would be
# owned by root and the containers could not write into them.
mkdir -p data/jobs models/popmusic

docker compose up -d --build
docker compose ps
