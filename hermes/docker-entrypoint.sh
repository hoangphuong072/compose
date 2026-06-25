#!/bin/sh
set -eu

mkdir -p "$MINIHERMES_HOME" "$MINIHERMES_WORKSPACE"

if [ ! -f "$MINIHERMES_HOME/config.json" ]; then
  cp /app/config.sample.json "$MINIHERMES_HOME/config.json"
fi

if [ "$#" -eq 0 ]; then
  set -- telegram
fi

exec minihermes "$@"
