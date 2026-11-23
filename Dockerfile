FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp XDG_CONFIG_HOME=/tmp/fin-agent-config
WORKDIR /app
COPY requirements-web.lock.txt .
RUN pip install --no-cache-dir -r requirements-web.lock.txt
COPY fin_agent ./fin_agent
COPY webapp ./webapp
RUN useradd --uid 10001 --no-create-home appuser
USER 10001
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn webapp.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
