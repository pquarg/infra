variable "admin_ipv4_cidrs" {
  description = "Public IPv4 /32 addresses allowed to reach the Kubernetes API and the CloudFront app."
  type        = list(string)

  validation {
    condition = length(var.admin_ipv4_cidrs) > 0 && alltrue([
      for cidr in var.admin_ipv4_cidrs :
      can(cidrnetmask(cidr)) && endswith(cidr, "/32")
    ])
    error_message = "Provide at least one public IPv4 address with a /32 suffix."
  }
}
