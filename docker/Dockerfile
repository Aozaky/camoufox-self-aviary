# Camoufox + KasmVNC container for Milestone 1
# Base: Kasm Ubuntu Noble with built-in KasmVNC desktop
FROM kasmweb/core-ubuntu-noble:1.19.0@sha256:69730b0ee6a572635fad9f8cf3ecb3d40fb86e01000f964b2dab719c664f67f4

USER root

# Install Python 3.12 and pip (Noble has 3.12 available)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    dbus-x11 \
    xdg-utils \
    libgtk-3-0 \
    libasound2t64 \
    libdbus-glib-1-2 \
    libxt6 \
    libx11-xcb1 \
    && rm -rf /var/lib/apt/lists/*

# Create a venv for the automation tools (avoids PEP 668 issues)
ENV VENV_DIR=/opt/camoufox-venv
RUN python3 -m venv "$VENV_DIR" \
    && "$VENV_DIR/bin/pip" install --no-cache-dir --upgrade pip

COPY requirements.txt /opt/requirements.txt
RUN "$VENV_DIR/bin/pip" install --no-cache-dir -r /opt/requirements.txt
# Camoufox 0.5.9 browser_pin constrains fetch to its bundled release.
RUN "$VENV_DIR/bin/python" -m camoufox fetch
RUN "$VENV_DIR/bin/python" -c "from camoufox.pkgman import installed_verstr; assert installed_verstr() == '158.0.1-beta.41', 'browser release changed; review explicit pin'"
RUN mkdir -p /home/kasm-user/camoufox-profile \
    && chown -R 1000:1000 /home/kasm-user/.cache /home/kasm-user/camoufox-profile \
    && chmod 700 /home/kasm-user/camoufox-profile

# Put venv on PATH
ENV PATH="$VENV_DIR/bin:$PATH"

# Prepare directories for the kasm-user
ENV HOME_DEFAULT=/home/kasm-default-profile
RUN mkdir -p "$HOME_DEFAULT/camoufox-profile" \
    && mkdir -p "$HOME_DEFAULT/test-data" \
    && chown -R 1000:0 "$HOME_DEFAULT/camoufox-profile" \
    && chown -R 1000:0 "$HOME_DEFAULT/test-data"

# Copy test page and scripts
COPY test_page.html /opt/test_page.html
COPY custom_startup.sh /dockerstartup/custom_startup.sh
RUN chmod +x /dockerstartup/custom_startup.sh \
    && chown 1000:0 /dockerstartup/custom_startup.sh

# Set permissions for default profile
RUN chown -R 1000:0 "$HOME_DEFAULT" \
    && /dockerstartup/set_user_permission.sh "$HOME_DEFAULT"

# Runtime env
ENV HOME=/home/kasm-user
WORKDIR $HOME
COPY compatibility.txt /opt/compatibility.txt
RUN "$VENV_DIR/bin/pip" install --no-cache-dir -r /opt/compatibility.txt
USER 1000
