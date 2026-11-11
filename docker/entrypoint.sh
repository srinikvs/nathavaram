#!/bin/sh
# Start as root so a fresh volume can be made writable, then drop to appuser.
set -eu
mkdir -p /data
if [ "$(id -u)" = "0" ]; then
  chown -R appuser:appuser /data
  exec su -s /bin/sh appuser -c "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000"
fi
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
