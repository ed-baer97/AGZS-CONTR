FROM python:3.12-slim

WORKDIR /app
COPY shared /app/shared
COPY central-server /app/central-server
RUN pip install --no-cache-dir /app/shared /app/central-server \
    && adduser --disabled-password --gecos "" agzs
WORKDIR /app/central-server
USER agzs
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m agzs_server"]
