"""Hygiene (Airflow 3): Mondays 08:00, stale watchlist entries and client reporting dates soon.

Thin by design, like release_watch_dag: the task calls services.heartbeat.hygiene_job.
"""
import pendulum
from airflow.sdk import dag, task


@dag(dag_id="hygiene_dag",
     schedule="0 8 * * 1",                       # Mondays 08:00, New York time
     start_date=pendulum.datetime(2026, 10, 1, tz="America/New_York"),
     catchup=False, tags=["lantern", "cs3b"])
def hygiene():
    @task
    def review():
        from services.heartbeat import hygiene_job
        return hygiene_job()

    review()


hygiene()
