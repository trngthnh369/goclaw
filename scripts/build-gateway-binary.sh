#!/bin/sh
# Compile the gateway binary into a named volume (ext4 inside WSL). Writing the
# output through the Windows bind mount makes this take >10 minutes and get
# killed, so /out must be a docker volume.
#
# Flags mirror Dockerfile stage 1 exactly: without -ldflags the binary ships
# unstripped AND cmd.Version stays empty, so `goclaw version` stops reporting
# the release the container claims to run.
set -e
cd /src
echo "compiling goclaw ${VERSION} ..."
CGO_ENABLED=0 GOOS=linux \
  go build -ldflags="-s -w -X github.com/nextlevelbuilder/goclaw/cmd.Version=${VERSION}" \
  -o /out/goclaw .
ls -l /out/goclaw
sha256sum /out/goclaw
echo COMPILE_OK
