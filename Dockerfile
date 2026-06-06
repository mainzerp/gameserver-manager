FROM python:3.12-slim

WORKDIR /app

# Install multiple Java versions (for different Minecraft versions) and deps
# MC 1.16 and below: Java 8 | MC 1.18+: Java 17 | MC 1.20.5+: Java 21 | MC 25.x+: Java 25
# Each Java version is installed independently so unavailable versions (depending
# on the Debian release in the base image) don't fail the build.
RUN apt-get update && \
    apt-get install -y --no-install-recommends lib32gcc-s1 lib32stdc++6 curl ca-certificates && \
    for v in 8 17 21; do \
      apt-get install -y --no-install-recommends openjdk-${v}-jre-headless 2>/dev/null || true; \
    done && \
    rm -rf /var/lib/apt/lists/*

# Install Java 25 EA via Eclipse Adoptium (not yet in Debian repos)
# Falls back gracefully if the download URL changes
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "amd64" ]; then JDK_ARCH="x64"; else JDK_ARCH="$ARCH"; fi && \
    mkdir -p /opt/java && \
    curl -fsSL "https://api.adoptium.net/v3/binary/latest/25/ea/linux/${JDK_ARCH}/jdk/hotspot/normal/eclipse" \
      -o /tmp/jdk25.tar.gz && \
    tar -xzf /tmp/jdk25.tar.gz -C /opt/java && \
    mv /opt/java/jdk-25* /opt/java/jdk-25 && \
    rm -f /tmp/jdk25.tar.gz && \
    echo "Java 25 installed" || echo "WARN: Java 25 not available, MC 25.x+ will not work"

# Set Java 25 as default if available, otherwise Java 21
RUN if [ -x /opt/java/jdk-25/bin/java ]; then \
      ln -sf /opt/java/jdk-25/bin/java /usr/local/bin/java25; \
    fi && \
    update-alternatives --set java $(ls /usr/lib/jvm/java-21-openjdk-*/bin/java | head -1) || true

# Verify all Java versions (architecture-agnostic)
RUN for v in 8 17 21; do \
      path=$(ls /usr/lib/jvm/java-${v}-openjdk-*/bin/java 2>/dev/null | head -1); \
      [ -n "$path" ] && echo "=== Java ${v} ===" && $path -version 2>&1 || true; \
    done && \
    if [ -x /opt/java/jdk-25/bin/java ]; then \
      echo "=== Java 25 ===" && /opt/java/jdk-25/bin/java -version 2>&1; \
    fi

# Install SteamCMD (controlled via build arg)
ARG INSTALL_STEAMCMD=true
RUN if [ "$INSTALL_STEAMCMD" = "true" ]; then \
      mkdir -p /opt/steamcmd && \
      curl -sqL "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz" | tar -xzC /opt/steamcmd && \
      /opt/steamcmd/steamcmd.sh +quit; \
    fi
ENV GSM_STEAMCMD_PATH=/opt/steamcmd/steamcmd.sh

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Build Tailwind CSS (skip if pre-compiled file is already present in the repo)
RUN if [ ! -f ./app/static/css/tailwind.css ] || [ ! -s ./app/static/css/tailwind.css ]; then \
      curl -sLO https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-linux-x64 && \
      chmod +x tailwindcss-linux-x64 && \
      ./tailwindcss-linux-x64 -i ./app/static/css/input.css -o ./app/static/css/tailwind.css --minify && \
      rm tailwindcss-linux-x64; \
    else \
      echo "Using pre-compiled tailwind.css"; \
    fi

# Vendor external JS/CSS dependencies for offline use (skip if already present)
RUN if [ ! -f /app/app/static/js/chart.min.js ] || [ ! -s /app/app/static/js/chart.min.js ]; then \
      curl -sL "https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js" \
        -o /app/app/static/js/chart.min.js; \
    else echo "Using pre-vendored chart.min.js"; fi && \
    mkdir -p /app/app/static/vendor/codemirror/theme /app/app/static/vendor/codemirror/mode && \
    if [ ! -f /app/app/static/vendor/codemirror/codemirror.min.js ] || [ ! -s /app/app/static/vendor/codemirror/codemirror.min.js ]; then \
      curl -sL "https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/codemirror.min.css" \
        -o /app/app/static/vendor/codemirror/codemirror.min.css && \
      curl -sL "https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/theme/material-darker.min.css" \
        -o /app/app/static/vendor/codemirror/theme/material-darker.min.css && \
      curl -sL "https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/codemirror.min.js" \
        -o /app/app/static/vendor/codemirror/codemirror.min.js && \
      for mode in properties yaml javascript xml toml shell markdown; do \
        curl -sL "https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.18/mode/${mode}/${mode}.min.js" \
          -o /app/app/static/vendor/codemirror/mode/${mode}.min.js; \
      done; \
    else echo "Using pre-vendored CodeMirror"; fi

# Vendor Google Fonts for offline use (skip if already present)
RUN if [ ! -f /app/app/static/fonts/inter/inter-400.woff2 ] || [ ! -s /app/app/static/fonts/inter/inter-400.woff2 ]; then \
    mkdir -p /app/app/static/fonts/inter /app/app/static/fonts/jetbrains-mono && \
    curl -sL "https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuLyfAZ9hiA.woff2" \
      -o /app/app/static/fonts/inter/inter-400.woff2 && \
    curl -sL "https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuI6fAZ9hiA.woff2" \
      -o /app/app/static/fonts/inter/inter-500.woff2 && \
    curl -sL "https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuGKYAZ9hiA.woff2" \
      -o /app/app/static/fonts/inter/inter-600.woff2 && \
    curl -sL "https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuFuYAZ9hiA.woff2" \
      -o /app/app/static/fonts/inter/inter-700.woff2 && \
    curl -sL "https://fonts.gstatic.com/s/jetbrainsmono/v18/tDbY2o-flEEny0FZhsfKu5WU4zr3E_BX0PnT8RD8yKxjPVmUsaaDhw.woff2" \
      -o /app/app/static/fonts/jetbrains-mono/jetbrains-mono-400.woff2 && \
    curl -sL "https://fonts.gstatic.com/s/jetbrainsmono/v18/tDbY2o-flEEny0FZhsfKu5WU4zr3E_BX0PnT8RD8yKxTPlmUsaaDhw.woff2" \
      -o /app/app/static/fonts/jetbrains-mono/jetbrains-mono-500.woff2; \
  else echo "Using pre-vendored fonts"; fi

RUN mkdir -p /app/data /app/servers /app/certs

# Copy entrypoint script (generates cert on first run, reuses on subsequent runs)
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Create non-root user for running the application
RUN groupadd -r gsm && useradd -r -g gsm -d /app -s /sbin/nologin gsm && \
    chown -R gsm:gsm /app

EXPOSE 8443

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -fk https://localhost:8443/health || exit 1

USER gsm
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "main.py"]
