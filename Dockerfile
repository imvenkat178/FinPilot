FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 finpilot \
    && useradd --uid 10001 --gid finpilot --create-home finpilot

COPY --chown=finpilot:finpilot finpilot ./finpilot
COPY --chown=finpilot:finpilot alembic ./alembic
COPY --chown=finpilot:finpilot alembic.ini ./alembic.ini
RUN mkdir -p /app/.local && chown finpilot:finpilot /app/.local

USER finpilot
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=4s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request, urllib.parse; host=urllib.parse.urlparse(os.getenv('FINPILOT_PUBLIC_ORIGIN') or 'http://localhost').netloc; request=urllib.request.Request('http://127.0.0.1:8000/api/health', headers={'Host': host}); urllib.request.urlopen(request, timeout=3).read()"

CMD ["python", "-m", "uvicorn", "finpilot.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
