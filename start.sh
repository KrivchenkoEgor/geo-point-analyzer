#!/bin/bash
# start.sh — лёгкий запуск GeoPoint Analyzer
#
# Использование:
#   ./start.sh        — запустить сервер (сам остановит старый, если он работает)
#   ./start.sh stop   — остановить работающий сервер
#
# Зачем этот файл: чтобы не запоминать команды запуска и остановки.
# Просто ./start.sh — и сервер поднят с последней версией кода.

# 1. Переходим в папку проекта — чтобы скрипт работал из любого места
#    ($0 — путь к самому скрипту, dirname берёт его папку)
cd "$(dirname "$0")" || exit 1

# 2. Режим «stop» — останавливаем сервер
if [ "$1" = "stop" ]; then
  # Ищем процесс, слушающий порт 7860 (наш сервер), по PID
  PIDS=$(lsof -ti :7860 -sTCP:LISTEN)
  if [ -n "$PIDS" ]; then
    echo "Останавливаю сервер (PID: $PIDS)..."
    kill $PIDS
    echo "Сервер остановлен."
  else
    echo "Сервер и так не запущен."
  fi
  exit 0
fi

# 3. Проверяем, что виртуальное окружение на месте
if [ ! -d "venv" ]; then
  echo "Папка venv не найдена. Сначала установите окружение:"
  echo "   python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# 4. Если старый сервер уже занимает порт 7860 — останавливаем его по PID.
#    Иначе новый процесс умрёт: порт занят, и пользователь увидит старый код.
#    (Урок из LESSONS.md: убивать по PID через lsof, а не по имени процесса)
OLD_PIDS=$(lsof -ti :7860 -sTCP:LISTEN)
if [ -n "$OLD_PIDS" ]; then
  echo "Найден старый сервер (PID: $OLD_PIDS) — останавливаю, чтобы запустить свежий код..."
  kill $OLD_PIDS
  sleep 2   # даём порту освободиться
fi

# 5. Запускаем сервер (python app.py внутри сам поднимает uvicorn)
echo "Запускаю GeoPoint Analyzer..."
echo "   Откройте в браузере: http://127.0.0.1:7860"
echo "   Остановить: ./start.sh stop  (или Ctrl+C в этом окне)"
echo ""
source venv/bin/activate && python app.py
