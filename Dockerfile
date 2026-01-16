FROM tiangolo/uvicorn-gunicorn-fastapi:python3.9

ENV APP_MODULE=main:app

COPY ./requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --upgrade -r /app/requirements.txt

COPY ./main.py /app
COPY ./index.html /app
