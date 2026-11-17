#!/bin/zsh
cd "$(dirname "$0")"
python3 server.py > /tmp/church-translation-mvp.log 2>&1 &
SERVER_PID=$!
for i in {1..30}; do
  if curl -sf "http://127.0.0.1:8000/" >/dev/null 2>&1; then
    open "http://127.0.0.1:8000"
    break
  fi
  sleep 0.2
done
wait $SERVER_PID
