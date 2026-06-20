#!/usr/bin/env bash
# Generate Python gRPC stubs from the data-plane protos into priva_common.dataplane.v1.
#
# Proto sources mirror the Python package layout (protos/priva_common/dataplane/v1)
# so generated intra-package imports resolve as
#   from priva_common.dataplane.v1 import common_pb2
# with NO post-processing (protoletariat/sed). Run from anywhere:
#   ./protos/gen.sh
#
# Needs grpcio-tools (dev dep). Generated *_pb2.py / *_pb2_grpc.py / *.pyi are committed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VPY="${VPY:-$ROOT/.venv/bin/python}"
OUT="$ROOT/libs/common/src"

"$VPY" -m grpc_tools.protoc \
  -I "$ROOT/protos" \
  --python_out="$OUT" \
  --grpc_python_out="$OUT" \
  --pyi_out="$OUT" \
  "$ROOT"/protos/priva_common/dataplane/v1/*.proto

echo "codegen OK → $OUT/priva_common/dataplane/v1/"
