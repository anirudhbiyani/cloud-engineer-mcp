"""Synthetic but realistic tool catalog used by the eval harness.

Tool names mirror the shapes seen in the official AWS / Azure / GCP MCP
servers as of late 2025. Descriptions are kept concise on purpose — they
match the brevity of real MCP `Tool.description` fields. We deliberately do
NOT depend on the live cloud MCP servers at eval time: that would make the
eval slow, network-dependent, and break in CI.

When the upstream tool catalogs drift materially, regenerate this file by
running `cloud-engineer-mcp list-tools` against a freshly-installed gateway
and pruning to a representative ~80-tool subset.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogEntry:
    namespaced_name: str
    backend_id: str
    description: str


# A representative subset (~80 tools) across the three official cloud MCP
# servers. Enough to make Recall@K meaningful without dominating eval time.
CATALOG: tuple[CatalogEntry, ...] = (
    # ---------- AWS (cloud control API + S3 + Lambda + DynamoDB + IAM) ----------
    CatalogEntry(
        "aws_prod__create_resource",
        "aws_prod",
        "Create a new AWS resource via the Cloud Control API (any AWS::* type).",
    ),
    CatalogEntry(
        "aws_prod__update_resource",
        "aws_prod",
        "Update an existing AWS resource via the Cloud Control API.",
    ),
    CatalogEntry(
        "aws_prod__delete_resource", "aws_prod", "Delete an AWS resource via the Cloud Control API."
    ),
    CatalogEntry("aws_prod__get_resource", "aws_prod", "Get details of an existing AWS resource."),
    CatalogEntry(
        "aws_prod__list_resources",
        "aws_prod",
        "List all AWS resources of a given Cloud Control type.",
    ),
    CatalogEntry("aws_prod__list_buckets", "aws_prod", "List all S3 buckets in the account."),
    CatalogEntry(
        "aws_prod__create_bucket",
        "aws_prod",
        "Create an S3 bucket, optionally with versioning and lifecycle rules.",
    ),
    CatalogEntry("aws_prod__delete_bucket", "aws_prod", "Delete an S3 bucket and all its objects."),
    CatalogEntry("aws_prod__get_object", "aws_prod", "Retrieve an object from an S3 bucket."),
    CatalogEntry("aws_prod__put_object", "aws_prod", "Upload an object to an S3 bucket."),
    CatalogEntry(
        "aws_prod__list_lambda_functions", "aws_prod", "List Lambda functions in the account."
    ),
    CatalogEntry(
        "aws_prod__invoke_lambda",
        "aws_prod",
        "Invoke a Lambda function synchronously or asynchronously.",
    ),
    CatalogEntry(
        "aws_prod__deploy_lambda",
        "aws_prod",
        "Deploy or update a Lambda function from a zip or container image.",
    ),
    CatalogEntry(
        "aws_prod__describe_lambda_function",
        "aws_prod",
        "Get configuration and metadata for a Lambda function.",
    ),
    CatalogEntry(
        "aws_prod__list_dynamodb_tables", "aws_prod", "List DynamoDB tables in the account."
    ),
    CatalogEntry(
        "aws_prod__create_dynamodb_table",
        "aws_prod",
        "Create a DynamoDB table with the given schema and capacity mode.",
    ),
    CatalogEntry(
        "aws_prod__put_dynamodb_item", "aws_prod", "Insert or replace an item in a DynamoDB table."
    ),
    CatalogEntry(
        "aws_prod__query_dynamodb", "aws_prod", "Query a DynamoDB table or index by partition key."
    ),
    CatalogEntry(
        "aws_prod__list_ec2_instances", "aws_prod", "List EC2 instances in the current region."
    ),
    CatalogEntry(
        "aws_prod__describe_ec2_instance",
        "aws_prod",
        "Get detailed configuration of an EC2 instance.",
    ),
    CatalogEntry("aws_prod__start_ec2_instance", "aws_prod", "Start a stopped EC2 instance."),
    CatalogEntry("aws_prod__stop_ec2_instance", "aws_prod", "Stop a running EC2 instance."),
    CatalogEntry("aws_prod__list_iam_roles", "aws_prod", "List IAM roles in the account."),
    CatalogEntry(
        "aws_prod__create_iam_role",
        "aws_prod",
        "Create an IAM role with the given trust policy and permissions.",
    ),
    CatalogEntry(
        "aws_prod__attach_iam_policy", "aws_prod", "Attach a managed policy to an IAM role or user."
    ),
    CatalogEntry("aws_prod__list_rds_databases", "aws_prod", "List Amazon RDS database instances."),
    CatalogEntry(
        "aws_prod__describe_rds_instance",
        "aws_prod",
        "Get configuration and status of an RDS database instance.",
    ),
    CatalogEntry(
        "aws_prod__list_cloudwatch_metrics", "aws_prod", "List CloudWatch metrics and namespaces."
    ),
    CatalogEntry(
        "aws_prod__get_cloudwatch_metric", "aws_prod", "Fetch a CloudWatch metric time series."
    ),
    # ---------- Azure (storage + VMs + functions + cosmos + AD) ----------
    CatalogEntry(
        "az_prod__list_storage_accounts",
        "az_prod",
        "List Azure storage accounts in the subscription.",
    ),
    CatalogEntry(
        "az_prod__create_storage_account",
        "az_prod",
        "Create an Azure storage account with the given SKU and kind.",
    ),
    CatalogEntry("az_prod__delete_storage_account", "az_prod", "Delete an Azure storage account."),
    CatalogEntry(
        "az_prod__list_blobs", "az_prod", "List blobs in an Azure Blob Storage container."
    ),
    CatalogEntry(
        "az_prod__upload_blob", "az_prod", "Upload a blob to an Azure Blob Storage container."
    ),
    CatalogEntry(
        "az_prod__download_blob", "az_prod", "Download a blob from an Azure Blob Storage container."
    ),
    CatalogEntry(
        "az_prod__list_vms", "az_prod", "List Azure virtual machines in the subscription."
    ),
    CatalogEntry(
        "az_prod__create_vm",
        "az_prod",
        "Create an Azure virtual machine with the given size and image.",
    ),
    CatalogEntry("az_prod__start_vm", "az_prod", "Start a stopped Azure virtual machine."),
    CatalogEntry("az_prod__stop_vm", "az_prod", "Stop an Azure virtual machine."),
    CatalogEntry("az_prod__delete_vm", "az_prod", "Delete an Azure virtual machine."),
    CatalogEntry("az_prod__list_function_apps", "az_prod", "List Azure Function Apps."),
    CatalogEntry(
        "az_prod__deploy_function_app", "az_prod", "Deploy code to an Azure Function App."
    ),
    CatalogEntry("az_prod__list_cosmos_accounts", "az_prod", "List Azure Cosmos DB accounts."),
    CatalogEntry(
        "az_prod__query_cosmos_container",
        "az_prod",
        "Run a SQL query against an Azure Cosmos DB container.",
    ),
    CatalogEntry(
        "az_prod__list_resource_groups",
        "az_prod",
        "List Azure resource groups in the subscription.",
    ),
    CatalogEntry(
        "az_prod__create_resource_group",
        "az_prod",
        "Create an Azure resource group in a given location.",
    ),
    CatalogEntry("az_prod__list_ad_users", "az_prod", "List Azure Active Directory users."),
    CatalogEntry("az_prod__list_ad_groups", "az_prod", "List Azure Active Directory groups."),
    CatalogEntry(
        "az_prod__list_app_services", "az_prod", "List Azure App Services in the subscription."
    ),
    CatalogEntry("az_prod__deploy_app_service", "az_prod", "Deploy code to an Azure App Service."),
    CatalogEntry(
        "az_prod__list_aks_clusters", "az_prod", "List Azure Kubernetes Service clusters."
    ),
    CatalogEntry(
        "az_prod__get_aks_credentials",
        "az_prod",
        "Fetch kubeconfig credentials for an AKS cluster.",
    ),
    # ---------- GCP (storage + compute + functions + BigQuery + GKE + IAM) ----------
    CatalogEntry("gcp__list_buckets", "gcp", "List Google Cloud Storage buckets in the project."),
    CatalogEntry(
        "gcp__create_bucket",
        "gcp",
        "Create a Google Cloud Storage bucket with a given storage class and location.",
    ),
    CatalogEntry("gcp__delete_bucket", "gcp", "Delete a Google Cloud Storage bucket."),
    CatalogEntry("gcp__upload_object", "gcp", "Upload an object to a Google Cloud Storage bucket."),
    CatalogEntry(
        "gcp__list_compute_instances",
        "gcp",
        "List Google Compute Engine virtual machine instances.",
    ),
    CatalogEntry(
        "gcp__create_compute_instance",
        "gcp",
        "Create a Compute Engine VM instance with the given machine type.",
    ),
    CatalogEntry("gcp__delete_compute_instance", "gcp", "Delete a Compute Engine VM instance."),
    CatalogEntry("gcp__start_compute_instance", "gcp", "Start a stopped Compute Engine instance."),
    CatalogEntry("gcp__stop_compute_instance", "gcp", "Stop a running Compute Engine instance."),
    CatalogEntry("gcp__list_cloud_functions", "gcp", "List deployed Google Cloud Functions."),
    CatalogEntry(
        "gcp__deploy_cloud_function", "gcp", "Deploy a Google Cloud Function from source."
    ),
    CatalogEntry("gcp__run_bigquery_query", "gcp", "Run a SQL query against BigQuery."),
    CatalogEntry("gcp__list_bigquery_datasets", "gcp", "List BigQuery datasets in the project."),
    CatalogEntry("gcp__create_bigquery_dataset", "gcp", "Create a BigQuery dataset."),
    CatalogEntry("gcp__list_gke_clusters", "gcp", "List Google Kubernetes Engine clusters."),
    CatalogEntry(
        "gcp__create_gke_cluster", "gcp", "Create a GKE cluster with the given node pool config."
    ),
    CatalogEntry(
        "gcp__get_gke_credentials", "gcp", "Fetch kubeconfig credentials for a GKE cluster."
    ),
    CatalogEntry(
        "gcp__list_iam_service_accounts", "gcp", "List IAM service accounts in the project."
    ),
    CatalogEntry(
        "gcp__create_service_account", "gcp", "Create a Google Cloud IAM service account."
    ),
    CatalogEntry("gcp__list_pubsub_topics", "gcp", "List Google Cloud Pub/Sub topics."),
    CatalogEntry("gcp__publish_pubsub_message", "gcp", "Publish a message to a Pub/Sub topic."),
    CatalogEntry("gcp__list_cloud_run_services", "gcp", "List Google Cloud Run services."),
    CatalogEntry("gcp__deploy_cloud_run", "gcp", "Deploy a container image to Cloud Run."),
    CatalogEntry("gcp__list_sql_instances", "gcp", "List Google Cloud SQL database instances."),
    # ---------- Kubernetes (per-context) ----------
    CatalogEntry("k8s_prod__list_pods", "k8s_prod", "List pods in a Kubernetes namespace."),
    CatalogEntry(
        "k8s_prod__describe_pod",
        "k8s_prod",
        "Show detailed configuration and status of a Kubernetes pod.",
    ),
    CatalogEntry("k8s_prod__exec_pod", "k8s_prod", "Execute a command inside a Kubernetes pod."),
    CatalogEntry("k8s_prod__get_logs", "k8s_prod", "Stream container logs from a Kubernetes pod."),
    CatalogEntry(
        "k8s_prod__apply_manifest", "k8s_prod", "Apply a Kubernetes YAML manifest to the cluster."
    ),
    CatalogEntry(
        "k8s_prod__rollout_restart",
        "k8s_prod",
        "Restart a Kubernetes deployment or statefulset rollout.",
    ),
    CatalogEntry(
        "k8s_prod__scale_deployment",
        "k8s_prod",
        "Scale a Kubernetes deployment to a number of replicas.",
    ),
    CatalogEntry("k8s_prod__list_services", "k8s_prod", "List Kubernetes services in a namespace."),
    CatalogEntry(
        "k8s_prod__helm_install", "k8s_prod", "Install a Helm chart into a Kubernetes cluster."
    ),
    CatalogEntry("k8s_prod__helm_list", "k8s_prod", "List installed Helm releases."),
    # ---------- Cloudflare ----------
    CatalogEntry(
        "cloudflare__list_workers", "cloudflare", "List Cloudflare Workers in the account."
    ),
    CatalogEntry("cloudflare__deploy_worker", "cloudflare", "Deploy a Cloudflare Worker script."),
    CatalogEntry(
        "cloudflare__list_dns_records", "cloudflare", "List DNS records for a Cloudflare zone."
    ),
    CatalogEntry(
        "cloudflare__create_dns_record", "cloudflare", "Create a DNS record in a Cloudflare zone."
    ),
    CatalogEntry(
        "cloudflare__purge_cache", "cloudflare", "Purge cached files from Cloudflare edge."
    ),
    CatalogEntry(
        "cloudflare__list_r2_buckets", "cloudflare", "List Cloudflare R2 object storage buckets."
    ),
    # ---------- DigitalOcean ----------
    CatalogEntry(
        "digitalocean__list_droplets", "digitalocean", "List DigitalOcean droplet virtual machines."
    ),
    CatalogEntry("digitalocean__create_droplet", "digitalocean", "Create a DigitalOcean droplet."),
    CatalogEntry(
        "digitalocean__list_k8s_clusters",
        "digitalocean",
        "List DigitalOcean managed Kubernetes clusters.",
    ),
    CatalogEntry(
        "digitalocean__list_spaces",
        "digitalocean",
        "List DigitalOcean Spaces (S3-compatible object storage).",
    ),
    # ---------- Azure DevOps ----------
    CatalogEntry(
        "ado_main__list_repos", "ado_main", "List Azure DevOps git repositories in a project."
    ),
    CatalogEntry(
        "ado_main__list_pull_requests",
        "ado_main",
        "List active pull requests in an Azure DevOps repository.",
    ),
    CatalogEntry(
        "ado_main__create_pull_request", "ado_main", "Open a new Azure DevOps pull request."
    ),
    CatalogEntry(
        "ado_main__list_work_items",
        "ado_main",
        "List Azure DevOps work items matching a WIQL query.",
    ),
    CatalogEntry("ado_main__run_pipeline", "ado_main", "Trigger an Azure DevOps pipeline run."),
    # ---------- GitHub (remote) ----------
    CatalogEntry(
        "gh_remote__list_repos", "gh_remote", "List GitHub repositories the user has access to."
    ),
    CatalogEntry(
        "gh_remote__list_pull_requests",
        "gh_remote",
        "List open pull requests on a GitHub repository.",
    ),
    CatalogEntry("gh_remote__create_pull_request", "gh_remote", "Open a new GitHub pull request."),
    CatalogEntry("gh_remote__list_issues", "gh_remote", "List GitHub issues on a repository."),
    CatalogEntry("gh_remote__run_workflow", "gh_remote", "Dispatch a GitHub Actions workflow."),
    # ---------- Microsoft Learn (remote, anonymous) ----------
    CatalogEntry(
        "mslearn__search_docs", "mslearn", "Search official Microsoft and Azure documentation."
    ),
    CatalogEntry(
        "mslearn__get_article",
        "mslearn",
        "Fetch the full text of a Microsoft Learn article by URL.",
    ),
    # ---------- AWS Knowledge / AWS managed (remote) ----------
    CatalogEntry(
        "aws_kb__search_docs", "aws_kb", "Search the official AWS documentation and API references."
    ),
    CatalogEntry(
        "aws_rem__describe_service", "aws_rem", "Describe an AWS service via the managed MCP proxy."
    ),
    # ---------- GCP managed remote MCPs ----------
    CatalogEntry(
        "gcp_rem_bq__run_query",
        "gcp_rem_bq",
        "Run a SQL query against BigQuery via the managed MCP endpoint.",
    ),
    CatalogEntry(
        "gcp_rem_bq__list_datasets",
        "gcp_rem_bq",
        "List BigQuery datasets via the managed MCP endpoint.",
    ),
    CatalogEntry(
        "gcp_rem_gke__list_clusters",
        "gcp_rem_gke",
        "List GKE clusters via the managed MCP endpoint.",
    ),
    CatalogEntry(
        "gcp_rem_storage__list_buckets",
        "gcp_rem_storage",
        "List GCS buckets via the managed MCP endpoint.",
    ),
    CatalogEntry(
        "gcp_rem_logging__query_logs",
        "gcp_rem_logging",
        "Query Cloud Logging entries via the managed MCP endpoint.",
    ),
)


def catalog_size() -> int:
    return len(CATALOG)
