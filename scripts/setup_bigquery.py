"""One-time BigQuery dataset and table creation script."""

import sys
sys.path.insert(0, ".")

from google.cloud import bigquery
from google.oauth2 import service_account

from src.config import get_settings


def create_dataset_and_tables():
    settings = get_settings()
    credentials = service_account.Credentials.from_service_account_file(
        settings.bigquery.bq_credentials_path
    )
    client = bigquery.Client(project=settings.bigquery.bq_project_id, credentials=credentials)

    dataset_ref = f"{settings.bigquery.bq_project_id}.{settings.bigquery.bq_dataset_id}"

    # Create dataset
    dataset = bigquery.Dataset(dataset_ref)
    dataset.location = "asia-northeast3"  # Seoul
    dataset = client.create_dataset(dataset, exists_ok=True)
    print(f"Dataset '{dataset_ref}' ready.")

    # --- ad_performance table ---
    ad_perf_schema = [
        bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("channel", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("account_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("campaign_id", "STRING"),
        bigquery.SchemaField("campaign_name", "STRING"),
        bigquery.SchemaField("ad_group_id", "STRING"),
        bigquery.SchemaField("ad_group_name", "STRING"),
        bigquery.SchemaField("record_type", "STRING"),
        bigquery.SchemaField("keyword", "STRING"),
        bigquery.SchemaField("match_type", "STRING"),
        bigquery.SchemaField("impressions", "INT64"),
        bigquery.SchemaField("clicks", "INT64"),
        bigquery.SchemaField("cost", "NUMERIC"),
        bigquery.SchemaField("cost_original", "NUMERIC"),
        bigquery.SchemaField("cost_currency", "STRING"),
        bigquery.SchemaField("exchange_rate", "NUMERIC"),
        bigquery.SchemaField("conversions", "NUMERIC"),
        bigquery.SchemaField("conversion_value", "NUMERIC"),
        bigquery.SchemaField("data_source", "STRING"),
        bigquery.SchemaField("ingested_at", "TIMESTAMP"),
        bigquery.SchemaField("raw_payload", "JSON"),
    ]

    ad_perf_table = bigquery.Table(f"{dataset_ref}.ad_performance", schema=ad_perf_schema)
    ad_perf_table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="date",
    )
    ad_perf_table.clustering_fields = ["channel", "account_id", "campaign_id"]
    client.create_table(ad_perf_table, exists_ok=True)
    print("Table 'ad_performance' ready (partitioned by date, clustered by channel/account/campaign).")

    # --- pipeline_runs table ---
    runs_schema = [
        bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("channel", "STRING"),
        bigquery.SchemaField("report_date_start", "DATE"),
        bigquery.SchemaField("report_date_end", "DATE"),
        bigquery.SchemaField("status", "STRING"),
        bigquery.SchemaField("rows_loaded", "INT64"),
        bigquery.SchemaField("error_message", "STRING"),
        bigquery.SchemaField("started_at", "TIMESTAMP"),
        bigquery.SchemaField("completed_at", "TIMESTAMP"),
        bigquery.SchemaField("data_source", "STRING"),
    ]

    runs_table = bigquery.Table(f"{dataset_ref}.pipeline_runs", schema=runs_schema)
    client.create_table(runs_table, exists_ok=True)
    print("Table 'pipeline_runs' ready.")

    # --- ad_performance_view ---
    view_ref = f"{dataset_ref}.ad_performance_view"
    view = bigquery.Table(view_ref)
    view.view_query = f"""
    SELECT *,
        SAFE_DIVIDE(clicks, impressions) AS ctr,
        SAFE_DIVIDE(cost, clicks) AS cpc,
        SAFE_DIVIDE(conversion_value, cost) AS roas
    FROM `{dataset_ref}.ad_performance`
    """
    client.create_table(view, exists_ok=True)
    print("View 'ad_performance_view' ready (CTR, CPC, ROAS auto-calculated).")

    print("\nBigQuery setup complete!")


if __name__ == "__main__":
    create_dataset_and_tables()
