# =============================================================================
# Parsers service image
# -----------------------------------------------------------------------------
# Default command: the long-running daemon (used by docker compose).
# One-shot mode  : override the command with `["python", "-m", "chn_parsers", "run"]`
#                  (used by Cloud Run Jobs / cron).
# =============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# tzdata -> Europe/Berlin timezone rules for zoneinfo
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 scraper

WORKDIR /app
COPY pyproject.toml README.md ./
COPY chn_parsers ./chn_parsers
RUN pip install --no-compile .

USER scraper
CMD ["python", "-m", "chn_parsers", "daemon"]

