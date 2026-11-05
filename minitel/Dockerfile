# Une recette pour tout. Home Assistant construit la dernière étape,
# docker-compose choisit les siennes avec target.

# On compile seulement app_softmodem contre le paquet Ubuntu (Asterisk 20) :
# un seul fichier C, là où reconstruire Asterisk prendrait de longues minutes.
FROM ubuntu:24.04 AS softmodem

ARG SOFTMODEM_COMMIT=596f2b6111bd5e79d10da128e89aa42b7b1285dc

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asterisk-dev ca-certificates curl gcc libc6-dev libspandsp-dev libtiff-dev
RUN curl -fsSL "https://github.com/irrelevantdotcom/asterisk-Softmodem/archive/${SOFTMODEM_COMMIT}.tar.gz" \
        | tar -xz --strip-components=1 -C /tmp \
    && gcc -O2 -fPIC -shared \
        -DASTMM_LIBC=ASTMM_IGNORE \
        -DAST_MODULE_SELF_SYM=__internal_app_softmodem_self \
        '-DAST_MODULE="app_softmodem"' \
        -o /tmp/app_softmodem.so /tmp/app_softmodem.c -lspandsp -ltiff


FROM python:3.12-slim AS service

WORKDIR /app
COPY service/pyproject.toml ./
COPY service/minitel_ha ./minitel_ha
RUN pip install --no-cache-dir .

USER nobody
CMD ["python", "-m", "minitel_ha"]


FROM ubuntu:24.04 AS asterisk

RUN apt-get update \
    && apt-get install -y --no-install-recommends asterisk libspandsp2t64 libtiff6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=softmodem /tmp/app_softmodem.so /tmp/app_softmodem.so
RUN mv /tmp/app_softmodem.so "$(dirname "$(dpkg -L asterisk-modules | grep '/app_dial.so$')")/"

COPY asterisk/extensions.conf asterisk/manager.conf asterisk/modules.conf asterisk/pjsip.conf asterisk/rtp.conf /etc/asterisk/
COPY asterisk/entrypoint.sh /entrypoint.sh
RUN mv /etc/asterisk/pjsip.conf /etc/asterisk/pjsip.conf.in && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]


# App Home Assistant : Asterisk et le service dans le même conteneur.
FROM asterisk AS addon

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 jq python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY service/pyproject.toml ./
COPY service/minitel_ha ./minitel_ha
RUN python3 -m venv /opt/service && /opt/service/bin/pip install --no-cache-dir .

COPY run.sh /run.sh
RUN chmod +x /run.sh

ENTRYPOINT ["/run.sh"]
