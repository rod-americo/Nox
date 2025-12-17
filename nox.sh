#!/bin/bash
cd "$(dirname "$0")"
# Tenta rodar usando o python do .nox no home do usuário
nohup ~/.nox/bin/python nox.py > /dev/null 2>&1 &
exit
