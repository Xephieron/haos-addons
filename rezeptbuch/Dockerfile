ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG=C.UTF-8

# Python + Flask direkt aus dem Alpine-Paketmanager (kein pip nötig)
RUN apk add --no-cache python3 py3-flask

WORKDIR /app

COPY app.py /app/
COPY templates/ /app/templates/
COPY static/ /app/static/
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
