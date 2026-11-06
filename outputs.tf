output "reasoning_engine_id" {
  description = "Resource ID of the deployed Vertex AI Agent Runtime Reasoning Engine."
  value       = google_vertex_ai_reasoning_engine.googler_rsu_decision_engine.id
}

output "service_account_email" {
  description = "Least-privilege service account email for the RSU Decision Agent."
  value       = google_service_account.rsu_agent_runtime_sa.email
}
