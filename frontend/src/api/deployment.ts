import { apiClient } from "./client";

export interface DeploymentProfile {
  deployment_profile: string;
  single_user: boolean;
  registration_enabled: boolean;
  demo_login_enabled: boolean;
  sandbox_enabled: boolean;
}

/** Public deployment capability flags (no auth required, safe for the login page). */
export function fetchDeploymentProfile(): Promise<DeploymentProfile> {
  return apiClient.get<DeploymentProfile>("/deployment/profile", {
    auth: false,
  });
}
