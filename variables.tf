variable "project_id" {
  description = "Google Cloud Project ID hosting the Googler RSU Decision Agent."
  type        = string
}

variable "region" {
  description = "Google Cloud region for Vertex AI Agent Runtime and BigQuery analytics."
  type        = string
  default     = "us-east1"
}
