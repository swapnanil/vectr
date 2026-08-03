#!/bin/sh
echo "deploying $1..."
echo "done."
date -u +"%Y-%m-%dT%H:%M:%SZ target=$1" > .deploy-state
exit 0
