"""Monthly brief (Airflow 3): 07:30 on weekdays; on a release day, one brief per mandate, to Priya.

Thin by design, like release_watch_dag: the task calls services.heartbeat.brief_job, which
writes a brief only on a release day and only for mandates with newly held items.
"""
import pendulum
from airflow.sdk import dag, task


@dag(dag_id="monthly_brief_dag",
     schedule="30 7 * * 1-5",                    # weekdays 07:30, New York time
     start_date=pendulum.datetime(2026, 10, 1, tz="America/New_York"),
     catchup=False, tags=["lantern", "cs3b"])
def monthly_brief():
    @task
    def brief():
        from services.heartbeat import brief_job
        return brief_job()

    brief()


monthly_brief()
