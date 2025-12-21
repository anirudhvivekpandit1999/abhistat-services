#!/bin/bash

cd /root/Abhitech_Statistical_Tool_Backend

source venv/bin/activate

export PYTHONPATH=/root/Abhitech_Statistical_Tool_Backend:$PYTHONPATH

exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
