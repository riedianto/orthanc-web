FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY receiver.py ./

ENV PYTHONUNBUFFERED=1
ENV RECEIVER_AE_TITLE=ORTHANC
ENV DICOM_STORE_PORT=4242
ENV ORTHANC_DICOM_HOST=orthanc
ENV ORTHANC_DICOM_PORT=4242
ENV ORTHANC_DICOM_AET=ORTHANC
ENV WORKLISTS_DIR=/var/lib/orthanc/worklists

EXPOSE 4242

CMD ["python", "receiver.py"]
