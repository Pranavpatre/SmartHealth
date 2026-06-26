from celery import Celery
from celery.schedules import crontab

from config import get_settings

settings = get_settings()

celery_app = Celery(
    "smarthealth",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
    include=[
        "tasks.prediction_tasks",
        "tasks.scoring_tasks",
        "tasks.notification_tasks",
        "tasks.retraining_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "tasks.prediction_tasks.*": {"queue": "predictions"},
        "tasks.scoring_tasks.*": {"queue": "scoring"},
        "tasks.notification_tasks.*": {"queue": "notifications"},
        "tasks.retraining_tasks.*": {"queue": "default"},
    },
    beat_schedule={
        "district-prediction-scan": {
            "task": "tasks.prediction_tasks.run_district_prediction_scan",
            "schedule": 900,  # every 15 minutes
            "options": {"queue": "predictions"},
        },
        "health-scores-update": {
            "task": "tasks.scoring_tasks.run_health_scores",
            "schedule": 21600,  # every 6 hours
            "options": {"queue": "scoring"},
        },
        "model-drift-check": {
            "task": "tasks.retraining_tasks.check_model_drift",
            "schedule": crontab(hour=2, minute=0),  # daily 2 AM IST
            "options": {"queue": "default"},
        },
        "morning-digest": {
            "task": "tasks.notification_tasks.send_morning_digest",
            "schedule": crontab(hour=7, minute=0),  # daily 7 AM IST
            "options": {"queue": "notifications"},
        },
        "anomaly-scan": {
            "task": "tasks.scoring_tasks.run_anomaly_scan",
            "schedule": 3600,  # every hour
            "options": {"queue": "scoring"},
        },
    },
)
