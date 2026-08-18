FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

ENV AURON_MCP_TRANSPORT=streamable-http
ENV AURON_MCP_READ_ONLY=true
ENV AURON_ADAPTER=mock
ENV AURON_MCP_HTTP_HOST=0.0.0.0
ENV AURON_MCP_HTTP_PORT=8765

EXPOSE 8765

CMD ["auron-mcp"]
