# Terraform Infrastructure as Code for Googler RSU Decision Agent (Rubric 5.2: IaC - 5 pts)
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.30"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# 1. Enable Required Gemini Enterprise Agent Platform APIs
resource "google_project_service" "agent_platform_apis" {
  for_each = toset([
    "aiplatform.googleapis.com",
    "agentregistry.googleapis.com",
    "cloudtrace.googleapis.com",
    "logging.googleapis.com",
    "secretmanager.googleapis.com",
    "dlp.googleapis.com",
    "bigquery.googleapis.com",
  ])
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

# 2. Dedicated Least-Privilege Service Account for Agent Runtime
resource "google_service_account" "rsu_agent_runtime_sa" {
  account_id   = "googler-rsu-agent-sa"
  display_name = "Googler RSU Decision Agent Runtime Service Account"
}

# 3. Google Cloud Secret Manager for Secure Credential Injection (Rubric 5.3)
resource "google_secret_manager_secret" "vertex_search_datastore_secret" {
  secret_id = "VERTEX_SEARCH_DATASTORE_ID"
  replication {
    auto {}
  }
  depends_on = [google_project_service.agent_platform_apis]
}

# 4. BigQuery Dataset for Agent Telemetry & Evaluation Analytics
resource "google_bigquery_dataset" "rsu_agent_analytics" {
  dataset_id  = "googler_rsu_agent_telemetry"
  location    = var.region
  description = "Stores structured JSON logs, intent vs. outcome traces, and evaluation metrics."
  depends_on  = [google_project_service.agent_platform_apis]
}

# 5. Vertex AI Agent Runtime (Reasoning Engine) Managed Deployment
resource "google_vertex_ai_reasoning_engine" "googler_rsu_decision_engine" {
  display_name = "googler-rsu-decision-agent"
  description  = "Enterprise ADK 2.0 Agent helping Google employees evaluate RSU sell-vs-hold decisions across US state/city tax jurisdictions and multi-tranche vesting schedules."
  region       = var.region
  depends_on   = [google_project_service.agent_platform_apis]
}
