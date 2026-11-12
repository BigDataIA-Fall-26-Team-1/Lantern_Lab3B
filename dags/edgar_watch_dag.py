"""EDGAR watch (Airflow 3): every 30 minutes, a new 10-Q, 10-K or 8-K Item 2.02 from a held BDC.

Thin by design, like release_watch_dag: the task calls a plain function in our code
(services.heartbeat.edgar_job), so tests and the simulated day run the same logic without Airflow.
"""
import pendulum
from airflow.sdk import dag, task


@dag(dag_id="edgar_watch_dag",
     schedule="*/30 * * * *",                    # every 30 minutes
     start_date=pendulum.datetime(2026, 10, 1, tz="America/New_York"),
     catchup=False, tags=["lantern", "cs3b"])
def edgar_watch():
    @task
    def poll():
        from services.heartbeat import edgar_job
        return edgar_job()

    poll()


edgar_watch()
