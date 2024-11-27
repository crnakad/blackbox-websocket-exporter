FROM python:3-alpine
ADD ./ /
RUN pip3 install setuptools && python3 setup.py install
CMD [ "websocket_exporter" ]
