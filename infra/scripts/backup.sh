#!/usr/bin/env bash
# Полный бэкап приложения = ДВА СОГЛАСОВАННЫХ ОБЪЕКТА (07-deployment.md#бэкапы-и-данные,
# ADR-068 §8):
#   1) pgdata                — pg_dump БД crm;
#   2) documents-attachments — байты изображений документов (named volume).
#
# ⚠️ Бэкап одной только БД БОЛЬШЕ НЕ является бэкапом приложения: восстановление без
# volume даёт строки document_attachments без файлов ⇒ документы с БИТЫМИ картинками.
# Обратный рассинхрон (файлы без строк) даёт мусор, никем не адресуемый. БД о файлах
# ничего не знает и расхождение сама не починит (сверка — по checksum sha256).
#
# ⚠️⚠️ ОКНО ПРОСТОЯ. Согласованность обеспечивается ОСТАНОВКОЙ backend на время съёмки:
# пока он остановлен, в БД и на volume никто не пишет, поэтому дамп и tar относятся к
# одному состоянию. API недоступен всё время работы скрипта (обычно десятки секунд).
# postgres при этом НЕ останавливается — из него снимается дамп.
#
# Артефакты (в $ROOT/backups, с одной и той же временной меткой — их нельзя разлучать):
#   crm-<ts>.sql                     — дамп БД
#   documents-attachments-<ts>.tgz   — байты вложений
# Оба пишутся во временный <файл>.part и переименовываются ТОЛЬКО после успеха: частично
# записанный артефакт не должен выглядеть бэкапом (иначе восстановление «из бэкапа»
# молча даст обрезанную БД). Недоделанные .part удаляются в trap.
#
# Восстановление (оба артефакта ОДНОЙ метки).
# ⚠️ ПЕРЕД восстановлением убедитесь, что архив непустой: `tar tzf <файл>.tgz | wc -l` —
# первая же команда процедуры делает `rm -rf /data/*`, и восстановление из пустого архива
# СТИРАЕТ все вложения. Снятие архива это уже проверяет (см. sanity-check ниже), но при
# ручном восстановлении из чужого/старого файла проверьте сами.
#   docker compose ... stop backend
#   docker compose ... exec -T postgres psql -U "$POSTGRES_USER" "$POSTGRES_DB" < crm-<ts>.sql
#   docker run --rm -v crm_documents-attachments:/data -v "$PWD/backups":/backup:ro \
#       alpine:3.20 sh -c 'rm -rf /data/* && tar xzpf /backup/documents-attachments-<ts>.tgz -C /data'
#   docker compose ... start backend
# Права и владелец внутри архива сохраняются (tar -p): каталоги 0700, файлы 0600,
# uid:gid = 999:999. ⚠️ Числовой uid — ЧАСТЬ ФОРМАТА БЭКАПА: и в tar, и на volume лежит
# ЧИСЛО, а не имя. Поэтому uid/gid пользователя `app` ЗАПИНЕН в backend/Dockerfile
# (`--gid 999` / `--uid 999`); менять его нельзя, не перечиповав существующие volume'ы и
# архивы — иначе вложения 0600 станут нечитаемыми для backend (все картинки молча
# ломаются, а восстановление из бэкапа воспроизводит старый uid).
#
# ⚠️ Обязателен ПЕРЕД деплоем с миграциями. Откат 0031_servers_ssh_key_auth УДАЛЯЕТ строки
# серверов с auth_method='key' (ADR-067 §2) — здесь pre-deploy дамп не «рекомендация».
# Поэтому скрипт ОБЯЗАН работать на прод-хосте /opt/crm, куда дерево приезжает rsync'ом
# с --exclude='.git' (.github/workflows/ci.yml): ⛔ НИКАКИХ `git rev-parse` для поиска
# корня — там нет .git, и под `set -e` скрипт умер бы на первой же строке.
set -euo pipefail

# Корень репозитория/деплоя — от расположения самого скрипта (infra/scripts/ → ../..).
# Работает и в git-дереве, и в rsync-копии без .git.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE=(docker compose --env-file "$ROOT/.env" -f "$ROOT/infra/docker-compose.yml")

BACKUP_DIR="$ROOT/backups"
TS="$(date +%Y%m%d-%H%M%S)"
DB_OUT="$BACKUP_DIR/crm-$TS.sql"
ATT_OUT="$BACKUP_DIR/documents-attachments-$TS.tgz"
BACKEND_STOPPED=0

backend_cid() { "${COMPOSE[@]}" ps -aq backend 2>/dev/null | head -n1; }

# Ждём, пока backend снова станет healthy. `restart: unless-stopped` вручную
# остановленный контейнер НЕ поднимет, поэтому молчаливый провал start'а недопустим.
wait_backend_healthy() {
  local cid deadline st
  cid="$(backend_cid)"
  [ -n "$cid" ] || return 1
  deadline=$(( $(date +%s) + 180 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    st="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || true)"
    case "$st" in
      healthy|running) return 0 ;;
      exited|dead)     return 1 ;;
    esac
    sleep 3
  done
  return 1
}

# EXIT-trap чистит частичные артефакты и поднимает backend. INT/TERM переводятся в exit,
# чтобы EXIT-trap отработал и на Ctrl-C / `kill`: иначе прерывание между stop и start
# оставило бы приложение лежать (unless-stopped его не поднимет).
on_exit() {
  local rc=$?
  rm -f "$DB_OUT.part" "$ATT_OUT.part"
  if [ "$BACKEND_STOPPED" = "1" ]; then
    echo "[backup] аварийное завершение — поднимаю backend" >&2
    "${COMPOSE[@]}" start backend >/dev/null 2>&1 || true
    wait_backend_healthy || echo "[backup] ⚠️ backend НЕ поднялся — проверьте вручную (docker compose ... ps)" >&2
  fi
  exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Имя named-volume берём из РАЗОБРАННОГО compose (volumes.<x>.name), а не хардкодим:
# префикс проекта может быть переопределён COMPOSE_PROJECT_NAME. Разбор — настоящим
# JSON-парсером: grep по сериализованному JSON ломался бы от любого нового атрибута
# (driver/labels) в объявлении volume. Оверрайд ATTACHMENTS_VOLUME — на случай хоста
# без python3.
if [ -n "${ATTACHMENTS_VOLUME:-}" ]; then
  ATT_VOLUME="$ATTACHMENTS_VOLUME"
else
  if ! command -v python3 >/dev/null 2>&1; then
    echo "[backup] нужен python3 для разбора docker compose config, либо задайте" >&2
    echo "         ATTACHMENTS_VOLUME=<имя volume> (обычно crm_documents-attachments)" >&2
    exit 1
  fi
  ATT_VOLUME="$("${COMPOSE[@]}" config --format json \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["volumes"]["documents-attachments"]["name"])')"
fi
[ -n "$ATT_VOLUME" ] || { echo "[backup] не удалось определить имя volume documents-attachments" >&2; exit 1; }

# ⛔⛔ ОБЯЗАТЕЛЬНАЯ ПРОВЕРКА СУЩЕСТВОВАНИЯ. `docker run -v <имя>:/data` при НЕсуществующем
# имени НЕ падает — docker молча СОЗДАЁТ пустой volume и монтирует его. Без этой проверки
# опечатка в ATTACHMENTS_VOLUME или рассинхрон имени (COMPOSE_PROJECT_NAME, пересозданный
# volume) давали бы rc=0, бодрое «готово» и архив на ~87 байт с единственной записью './',
# рядом с честным дампом БД. Это ХУЖЕ отсутствия бэкапа: артефакт выглядит валидным, а
# процедура восстановления начинается с `rm -rf /data/*` ⇒ «восстановление» стёрло бы все
# вложения. Проверяем ДО остановки backend — падаем без простоя и без артефактов.
if ! docker volume inspect "$ATT_VOLUME" >/dev/null 2>&1; then
  echo "[backup] ⛔ volume '$ATT_VOLUME' не существует — бэкап НЕ снят." >&2
  echo "         docker молча создал бы пустой volume и архив выглядел бы валидным." >&2
  echo "         Проверьте имя: docker volume ls | grep documents-attachments" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

# Считает объекты на volume (сам /data + всё внутри). Читает :ro — остановки не требует.
count_volume_objects() {
  docker run --rm -v "$ATT_VOLUME":/data:ro alpine:3.20 sh -c 'find /data | wc -l' | tr -d ' '
}

# ГЕЙТ 2 — «volume существует, но ПУСТ» — ДО остановки backend (07-deployment.md §Инструмент
# бэкапа: проверка читает volume :ro, останавливать ради неё ничего не нужно). Иначе отказ
# по этому гейту стоил бы окна простоя и снятого впустую дампа при нулевом результате.
# Пустой volume — это либо честный первый деплой ADR-068, либо смонтирован не тот volume;
# по данным их не различить ⇒ fail-closed с одноразовым ручным оверрайдом.
EMPTY_ATTACHMENTS=0
if [ "$(count_volume_objects)" -le 1 ]; then
  if [ "${ALLOW_EMPTY_ATTACHMENTS:-0}" != "1" ]; then
    echo "[backup] ⛔ volume '$ATT_VOLUME' ПУСТ — вложений нет, бэкап НЕ снят (простоя не было)." >&2
    echo "         Если вложений действительно ещё нет (первый деплой ADR-068 — сначала bootstrap," >&2
    echo "         07-deployment.md §Первый деплой ADR-068), повторите прогон ВРУЧНУЮ с" >&2
    echo "         ALLOW_EMPTY_ATTACHMENTS=1. Иначе проверьте, тот ли volume смонтирован:" >&2
    echo "         docker volume ls; docker run --rm -v $ATT_VOLUME:/data:ro alpine:3.20 find /data" >&2
    exit 1
  fi
  EMPTY_ATTACHMENTS=1
  # В stderr: типовой cron (`… >/dev/null`) шлёт оператору только stderr — предупреждение
  # об отсутствии вложений обязано быть видно именно там (07-deployment.md §Инструмент бэкапа).
  echo "[backup] ⚠️ volume '$ATT_VOLUME' ПУСТ — архив вложений будет пустым;" >&2
  echo "         продолжаю только потому, что задан ALLOW_EMPTY_ATTACHMENTS=1 (одноразовый" >&2
  echo "         ручной оверрайд; ⛔ в cron/CI/обёртке деплоя он запрещён)." >&2
fi

echo "[backup] ⚠️ backend будет ОСТАНОВЛЕН на время съёмки (окно простоя API)"
"${COMPOSE[@]}" stop backend
BACKEND_STOPPED=1

# Эталон для сверки с архивом снимаем ТОЛЬКО ПОСЛЕ остановки backend: до неё он жив и может
# принять загрузку вложения между замером и tar'ом ⇒ ARC_COUNT > SRC_COUNT, и строгая сверка
# оборвала бы ИСПРАВНЫЙ бэкап (с уже снятым дампом) как «частичный/подменённый архив».
# После stop писателей нет — сверка детерминирована.
SRC_COUNT="$(count_volume_objects)"

echo "[backup] pg_dump → $DB_OUT"
# Креды читаются из env самого postgres-контейнера, не передаются с хоста.
"${COMPOSE[@]}" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$DB_OUT.part"

echo "[backup] documents-attachments ($ATT_VOLUME) → $ATT_OUT"
# Volume монтируется :ro — одноразовый контейнер физически не может испортить данные.
# alpine закреплён по тегу (без :latest) — единая pinning-политика проекта.
docker run --rm \
  -v "$ATT_VOLUME":/data:ro \
  -v "$BACKUP_DIR":/backup \
  alpine:3.20 \
  tar czpf "/backup/documents-attachments-$TS.tgz.part" -C /data .

# Sanity-check СОДЕРЖИМОГО (страховка второго уровня к inspect выше).
ARC_COUNT="$(tar tzf "$ATT_OUT.part" | wc -l | tr -d ' ')"
if [ "$ARC_COUNT" != "$SRC_COUNT" ]; then
  echo "[backup] ⛔ архив не совпал с источником: в volume $SRC_COUNT объектов, в архиве $ARC_COUNT." >&2
  echo "         Артефакты НЕ сохранены (частичный/подменённый архив опаснее его отсутствия)." >&2
  exit 1
fi
# Страховка на случай, если volume опустел МЕЖДУ гейтом 2 и остановкой backend (ошибочный
# rm, пересоздание volume): тот же fail-closed, тот же одноразовый оверрайд.
# ⚠️ EMPTY_ATTACHMENTS выставляем ЗДЕСЬ ТОЖЕ, а не только в гейте 2: при уже заданном
# ALLOW_EMPTY_ATTACHMENTS=1 этот путь проходит дальше, и без флага не появился бы ни
# маркер .EMPTY, ни предупреждающий финал — пустой архив выглядел бы здоровым. Это был
# последний остававшийся путь к «внешне здоровому» пустому бэкапу.
if [ "$ARC_COUNT" -le 1 ]; then
  if [ "${ALLOW_EMPTY_ATTACHMENTS:-0}" != "1" ]; then
    echo "[backup] ⛔ архив вложений пуст (volume '$ATT_VOLUME' опустел после проверки) — артефакты НЕ сохранены." >&2
    exit 1
  fi
  if [ "$EMPTY_ATTACHMENTS" != "1" ]; then
    echo "[backup] ⚠️ volume '$ATT_VOLUME' ОПУСТЕЛ уже после проверки — архив вложений пуст;" >&2
    echo "         прогон продолжен только из-за ALLOW_EMPTY_ATTACHMENTS=1. Это НЕ нормальная" >&2
    echo "         ситуация даже на первом деплое: volume был непустым за секунды до съёмки." >&2
  fi
  EMPTY_ATTACHMENTS=1
fi

# Оба артефакта проверены — только теперь снимаем суффикс .part. До этого момента любой
# сбой оставляет ТОЛЬКО .part-файлы, которые подчищает trap: полу-бэкап не должен
# существовать под именем, похожим на валидный.
mv "$DB_OUT.part" "$DB_OUT"
mv "$ATT_OUT.part" "$ATT_OUT"

# Пустой архив помечаем и НА ДИСКЕ: имена нормативных артефактов не меняем (07-deployment.md
# §Инструмент бэкапа фиксирует crm-<ts>.sql / documents-attachments-<ts>.tgz), но кладём
# рядом маркер — иначе через месяц пустой .tgz неотличим от здорового по одному листингу.
if [ "$EMPTY_ATTACHMENTS" = "1" ]; then
  printf '%s\n' \
    "Архив вложений ПУСТ (в нём только './')." \
    "Снят $TS с ALLOW_EMPTY_ATTACHMENTS=1: на момент съёмки volume '$ATT_VOLUME' не содержал вложений." \
    "⛔ Восстановление из него НЕ вернёт изображения документов, а процедура начинается с rm -rf /data/*." \
    > "$ATT_OUT.EMPTY"
fi

echo "[backup] start backend"
"${COMPOSE[@]}" start backend
BACKEND_STOPPED=0
if ! wait_backend_healthy; then
  echo "[backup] ⛔ backend не вернулся в рабочее состояние после бэкапа" >&2
  echo "         артефакты сняты: $DB_OUT / $ATT_OUT" >&2
  exit 1
fi

if [ "$EMPTY_ATTACHMENTS" = "1" ]; then
  # Финальная строка НЕ должна выглядеть как здоровый прогон. Дублируем в stderr: при
  # типовом cron `… >/dev/null` оператору видна только она.
  echo "[backup] готово (⚠️ ВЛОЖЕНИЙ НЕТ: архив пуст, снят по ALLOW_EMPTY_ATTACHMENTS=1) — метка $TS:"
  echo "[backup] готово, но ⚠️ ВЛОЖЕНИЙ НЕТ: архив $ATT_OUT пуст (ALLOW_EMPTY_ATTACHMENTS=1)." >&2
  echo "         Если это НЕ первый деплой ADR-068 — данные вложений потеряны, разберитесь ДО следующего прогона." >&2
else
  echo "[backup] готово (артефакты ОДНОЙ метки $TS, хранить/восстанавливать вместе):"
fi
echo "         $DB_OUT"
echo "         $ATT_OUT"
