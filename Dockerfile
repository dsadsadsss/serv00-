FROM python:3.8-slim-buster

WORKDIR /app

RUN apt-get update && apt-get install -y openssh-client
RUN pip install paramiko schedule flask

COPY vps_monitor.py .

EXPOSE 7860

CMD ["python", "-u", "vps_monitor.py"]
