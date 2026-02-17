#!/bin/bash
set -e

cd /home/site/wwwroot
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000
