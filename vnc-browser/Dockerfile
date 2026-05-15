FROM debian:bullseye-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99

RUN apt-get update && apt-get install -y --no-install-recommends \
    # Python
    python3 python3-pip \
    # Virtual display
    xvfb \
    # Browser — real deb package (Ubuntu 22.04 ships snap, which breaks in Docker)
    chromium \
    # Input + screenshot
    xdotool \
    scrot \
    # VNC for live observation
    x11vnc \
    # noVNC static files + websocket proxy
    novnc \
    python3-websockify \
    # Utils
    x11-utils \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir fastapi "uvicorn[standard]" pydantic websockets

# Preferences are reset at container start (in CMD), not here — so every run
# begins from a clean state even if Chromium mutated them during the last session.

COPY main.py /app/main.py
WORKDIR /app

EXPOSE 8080
# Browser control API (internal only — accessed by cookie-refresher via docker network)
EXPOSE 5900
# Raw VNC
EXPOSE 6080
# noVNC web viewer → http://localhost:6080/vnc.html?autoconnect=1&resize=scale

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -sf http://localhost:8080/health || exit 1

CMD ["sh", "-c", "\
  rm -f /tmp/.X99-lock && \
  mkdir -p /root/.config/chromium/Default && \
  printf '{\"devtools\":{\"preferences\":{\"currentDockState\":\"\\\"bottom\\\"\",\"panel-selectedTab\":\"\\\"network\\\"\"}}}\\n' \
    > /root/.config/chromium/Default/Preferences && \
  Xvfb :99 -screen 0 1600x1050x24 -ac +extension GLX +render -noreset & \
  sleep 3 && \
  x11vnc -display :99 -nopw -listen 0.0.0.0 -xkb -forever -shared -quiet & \
  websockify --web /usr/share/novnc 6080 localhost:5900 & \
  sleep 1 && \
  uvicorn main:app --host 0.0.0.0 --port 8080 \
"]
