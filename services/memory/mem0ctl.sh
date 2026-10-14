#!/bin/bash
# mem0ctl — Shared Brain kontrol aracı
# Qdrant native (launchd, always-on) + Dashboard (on-demand, :8899)

MEM0="$HOME/mem0-local"
QDRANT_URL="http://localhost:6333"
DASH_URL="http://localhost:8899"
DASH_PIDFILE="/tmp/mem0-dashboard.pid"

MCP_LABEL="com.mem0.mcp-http"
MCP_PLIST="$HOME/Library/LaunchAgents/$MCP_LABEL.plist"
MCP_TEMPLATE="$MEM0/launchd/$MCP_LABEL.plist.template"
MCP_PORT=8812

qdrant_up()   { curl -s -m 3 "$QDRANT_URL/collections" >/dev/null 2>&1; }
dash_up()     { curl -s -m 3 "$DASH_URL" >/dev/null 2>&1; }
# An unauthenticated probe: 401 is the healthy answer, since it proves the
# listener is up AND still demanding a token.
mcp_http_status() {
  /usr/bin/python3 - "$MCP_PORT" <<'PY' 2>/dev/null
import sys, urllib.error, urllib.request
url = "http://127.0.0.1:%s/mcp" % sys.argv[1]
req = urllib.request.Request(url, data=b"{}", method="POST",
                             headers={"Content-Type": "application/json",
                                      "Accept": "application/json, text/event-stream"})
try:
    with urllib.request.urlopen(req, timeout=3) as r:
        print("open:%d" % r.status)
except urllib.error.HTTPError as e:
    print("open:%d" % e.code)
except Exception:
    print("down")
PY
}

case "$1" in
  status|"")
    echo "── Shared Brain durumu ──"
    if qdrant_up; then
      CNT=$(curl -s -m 3 "$QDRANT_URL/collections/mem0_local" | python3 -c "import sys,json;print(json.load(sys.stdin)['result']['points_count'])" 2>/dev/null)
      echo "  Qdrant   : ✅ çalışıyor (:6333)  |  hafıza: ${CNT:-?} kayıt"
    else
      echo "  Qdrant   : ❌ kapalı — 'launchctl load -w ~/Library/LaunchAgents/com.mem0.qdrant.plist'"
    fi
    case "$(mcp_http_status)" in
      open:401) echo "  MCP HTTP : ✅ çalışıyor (127.0.0.1:$MCP_PORT, auth zorunlu)" ;;
      open:*)   echo "  MCP HTTP : ⚠️  çalışıyor ama token'sız istek reddedilmedi — kontrol et!" ;;
      *)        echo "  MCP HTTP : ⚪ kapalı (kur: mem0 http-install)" ;;
    esac
    if dash_up; then echo "  Dashboard: ✅ açık → $DASH_URL"; else echo "  Dashboard: ⚪ kapalı (başlat: mem0 dash)"; fi
    echo -n "  Ollama   : "; curl -s -m 3 http://localhost:11434/api/version >/dev/null 2>&1 && echo "✅ çalışıyor" || echo "⚪ kapalı"
    ;;

  dash)
    if dash_up; then echo "Dashboard zaten açık → $DASH_URL"; else
      echo "Dashboard başlatılıyor..."
      nohup "$MEM0/bin/mem0-python" "$MEM0/dashboard/server.py" > /tmp/mem0-dashboard.log 2>&1 &
      echo $! > "$DASH_PIDFILE"
      sleep 2
      dash_up && echo "✅ açıldı → $DASH_URL" || { echo "❌ açılamadı, log:"; tail -5 /tmp/mem0-dashboard.log; }
    fi
    command -v open >/dev/null && open "$DASH_URL"
    ;;

  dash-stop)
    if [ -f "$DASH_PIDFILE" ]; then kill "$(cat "$DASH_PIDFILE")" 2>/dev/null && echo "Dashboard durduruldu"; rm -f "$DASH_PIDFILE"
    else pkill -f "dashboard/server.py" && echo "Dashboard durduruldu" || echo "Dashboard zaten kapalı"; fi
    ;;

  http-install)
    # Render the tracked template (launchd cannot expand variables itself) and
    # hand the result to launchd. Idempotent: unload first so re-running picks
    # up template changes.
    [ -f "$MCP_TEMPLATE" ] || { echo "şablon yok: $MCP_TEMPLATE"; exit 1; }
    if [ ! -f "$HOME/.mem0/mcp-token" ] && [ -z "${MEM0_MCP_TOKEN:-}" ]; then
      echo "önce token oluştur:"
      echo "  ( umask 077; openssl rand -hex 32 > ~/.mem0/mcp-token )"
      exit 1
    fi
    mkdir -p "$MEM0/logs" "$HOME/Library/LaunchAgents"
    sed "s|__MEM0_ROOT__|$MEM0|g" "$MCP_TEMPLATE" > "$MCP_PLIST"
    launchctl unload "$MCP_PLIST" 2>/dev/null
    # Free the port from any hand-started instance — by listener, NOT by script
    # name. `pkill -f mcp_server.py` would also kill the stdio servers that
    # Claude Code spawns as its own children, cutting the local MCP connection
    # of whoever happens to be working at the time.
    lsof -ti "tcp:$MCP_PORT" -sTCP:LISTEN 2>/dev/null | xargs -r kill 2>/dev/null
    sleep 1
    launchctl load -w "$MCP_PLIST" && echo "yüklendi: $MCP_PLIST"
    sleep 4
    case "$(mcp_http_status)" in
      open:401) echo "✅ MCP HTTP ayakta ve auth istiyor" ;;
      open:*)   echo "⚠️  ayakta ama auth zorunlu görünmüyor — durdur ve incele" ;;
      *)        echo "❌ ayağa kalkmadı, log:"; tail -5 "$MEM0/logs/mcp-http.err.log" 2>/dev/null ;;
    esac
    ;;

  http-uninstall)
    launchctl unload -w "$MCP_PLIST" 2>/dev/null && echo "kaldırıldı: $MCP_LABEL" || echo "zaten yüklü değil"
    ;;

  qdrant-restart)
    launchctl unload ~/Library/LaunchAgents/com.mem0.qdrant.plist 2>/dev/null
    launchctl load -w ~/Library/LaunchAgents/com.mem0.qdrant.plist && echo "Qdrant yeniden yüklendi"
    ;;

  reflexion)
    echo "── Reflexion Sentezleyici Çalıştırılıyor ──"
    shift
    "$MEM0/bin/mem0-python" "$MEM0/reflexion_engine.py" "$@"
    ;;

  indexes)
    echo "── Qdrant Payload İndeksleri Kuruluyor ──"
    "$MEM0/bin/mem0-python" "$MEM0/qdrant_indexes.py"
    ;;

  tokens)
    shift
    "$MEM0/bin/mem0-python" "$MEM0/mem.py" tokens "$@"
    ;;

  graph)
    shift
    "$MEM0/bin/mem0-python" "$MEM0/mem.py" graph "$@"
    ;;

  skills)
    shift
    "$MEM0/bin/mem0-python" "$MEM0/mem.py" skills "$@"
    ;;

  harness)
    shift
    "$MEM0/bin/mem0-python" "$MEM0/mem.py" harness "$@"
    ;;

  compact)
    shift
    "$MEM0/bin/mem0-python" "$MEM0/mem.py" compact "$@"
    ;;

  sweep)
    shift
    "$MEM0/bin/mem0-python" "$MEM0/mem.py" sweep "$@"
    ;;

  ide)
    shift
    "$MEM0/bin/mem0-python" "$MEM0/mem.py" ide "$@"
    ;;

  health)
    shift
    "$MEM0/bin/mem0-python" "$MEM0/mem.py" health "$@"
    ;;

  dream|pipeline)
    shift
    echo "── Central Brain Bakım & Konsolidasyon Pipeline (Dream Daemon) ──"
    "$MEM0/bin/mem0-python" "$MEM0/pipeline.py" "$@"
    ;;

  obsidian)
    shift
    "$MEM0/bin/mem0-python" "$MEM0/mem.py" obsidian "$@"
    ;;

  ask|chat|assistant)
    shift
    "$MEM0/bin/mem0-python" "$MEM0/assistant.py" "$@"
    ;;

  telegram)
    shift
    exec "$MEM0/bin/mem0-python" "$MEM0/telegram_bot.py" "$@"
    ;;

  install-pi)
    shift
    exec "$MEM0/scripts/install-pi.sh" "$@"
    ;;

  *)
    echo "Kullanım: mem0 [status|dash|dash-stop|qdrant-restart|http-install|http-uninstall|reflexion|indexes|tokens|graph|skills|harness|compact|sweep|ide|health|dream|pipeline|obsidian|ask|chat|telegram|install-pi]"
    ;;
esac


