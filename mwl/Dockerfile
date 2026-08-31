FROM python:3.11-slim

# Install Microsoft ODBC Driver 18 for SQL Server
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg2 \
    apt-transport-https \
    unixodbc-dev && \
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg && \
    curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY mwl_server.py ./
COPY polling_engine.py ./

ENV PYTHONUNBUFFERED=1
ENV MWL_AE_TITLE=ORTHANC
ENV MWL_PORT=4242
ENV WORKLISTS_DIR=/var/lib/orthanc/worklists
ENV HMS_SQLSERVER_HOST=localhost
ENV HMS_SQLSERVER_PORT=1433
ENV HMS_SQLSERVER_DB=artha_medika
ENV HMS_SQLSERVER_USER=sa
ENV HMS_SQLSERVER_PASSWORD=secret

EXPOSE 4242

CMD ["python", "mwl_server.py"]
