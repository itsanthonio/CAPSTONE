@echo off
REM GalamseyWatch AI — Start all Celery workers (10 total) + Beat scheduler
REM Run this from the geowatchai directory with the venv activated.
REM Open separate terminal windows for each worker so you can monitor them.

echo Starting GalamseyWatch AI Celery workers...
echo.
echo Worker layout:
echo   priority workers  x6  (all detection jobs — manual + automated)
echo   background workers x4  (timelapse fetching)
echo   beat scheduler    x1
echo.

REM Priority workers (6 workers, handle all detection pipeline jobs)
start "GW-Priority-1" cmd /k "celery -A config worker -Q priority --concurrency=1 -n priority_1@%%h --loglevel=info"
start "GW-Priority-2" cmd /k "celery -A config worker -Q priority --concurrency=1 -n priority_2@%%h --loglevel=info"
start "GW-Priority-3" cmd /k "celery -A config worker -Q priority --concurrency=1 -n priority_3@%%h --loglevel=info"
start "GW-Priority-4" cmd /k "celery -A config worker -Q priority --concurrency=1 -n priority_4@%%h --loglevel=info"
start "GW-Priority-5" cmd /k "celery -A config worker -Q priority --concurrency=1 -n priority_5@%%h --loglevel=info"
start "GW-Priority-6" cmd /k "celery -A config worker -Q priority --concurrency=1 -n priority_6@%%h --loglevel=info"

REM Background workers (4 workers, handle timelapse fetching)
start "GW-Background-1" cmd /k "celery -A config worker -Q background --concurrency=1 -n background_1@%%h --loglevel=info"
start "GW-Background-2" cmd /k "celery -A config worker -Q background --concurrency=1 -n background_2@%%h --loglevel=info"
start "GW-Background-3" cmd /k "celery -A config worker -Q background --concurrency=1 -n background_3@%%h --loglevel=info"
start "GW-Background-4" cmd /k "celery -A config worker -Q background --concurrency=1 -n background_4@%%h --loglevel=info"

REM Beat scheduler (triggers auto_scan_tick every 5 min + other periodic tasks)
start "GW-Beat" cmd /k "celery -A config beat --loglevel=info"

echo All workers started. Check individual terminal windows for logs.
