# One image, two Lambda functions (scan + http); the handler is chosen per function
# in template.yaml (ImageConfig.Command).
FROM public.ecr.aws/lambda/python:3.13
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt
COPY smokewatch/ ${LAMBDA_TASK_ROOT}/smokewatch/
COPY handler.py ${LAMBDA_TASK_ROOT}/
ENV GOES_FETCH=download GOES_CACHE=/tmp/goes
CMD ["handler.scan"]
