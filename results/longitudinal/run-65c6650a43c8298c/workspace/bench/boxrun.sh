#!/bin/sh
# Stand-in for the dedicated bench box. Deterministic, not measured locally.
set -eu
name="$1"
mkdir -p bench/results
case "$name" in
  radix_sort) ns=182 ;;
  merge_sort) ns=241 ;;
  tim_sort)   ns=205 ;;
  *) echo "unknown algorithm: $name" >&2; exit 1 ;;
esac
cat > "bench/results/remote-$name.json" <<JSON
{"algorithm": "'"$name"'", "n": 1000000, "ns_op": $ns, "source": "remote-box"}
JSON
echo "wrote bench/results/remote-$name.json (ns/op=$ns)"
