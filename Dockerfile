FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && groupadd --system studio \
    && useradd --system --gid studio --no-create-home studio
COPY app ./app
COPY examples/demo.pdf ./examples/demo.pdf
COPY LICENSE THIRD_PARTY_NOTICES.md ./
USER studio
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
