# The controller owns this ALB and its target groups. Look it up after app sync;
# never import it into Terraform or give it a second lifecycle manager.
data "aws_lb" "myfastapiapp_origin" {
  name = "deployment-project-dev-app"

  lifecycle {
    postcondition {
      condition = (
        self.internal &&
        self.load_balancer_type == "application" &&
        self.ip_address_type == "ipv4" &&
        self.vpc_id == module.vpc.vpc_id &&
        contains(self.security_groups, aws_security_group.myfastapiapp_alb.id)
      )
      error_message = "The app origin must be the internal IPv4 ALB in this VPC with the Terraform-managed frontend group attached."
    }
  }
}

resource "aws_cloudfront_vpc_origin" "myfastapiapp" {
  vpc_origin_endpoint_config {
    name                   = "deployment-project-dev-myfastapiapp"
    arn                    = data.aws_lb.myfastapiapp_origin.arn
    http_port              = 80
    https_port             = 443 # Required endpoint metadata; HTTP-only does not use this port.
    origin_protocol_policy = "http-only"

    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }

  timeouts {
    create = "30m"
    update = "30m"
    delete = "30m"
  }

  # Preserve the origin's network access and platform controllers until the VPC
  # origin is removed. App Ingress cleanup still needs an explicit GitOps step.
  depends_on = [
    aws_vpc_security_group_ingress_rule.myfastapiapp_from_cloudfront,
    aws_vpc_security_group_egress_rule.myfastapiapp_to_workers,
    helm_release.load_balancer_controller,
    helm_release.argocd,
  ]
}

output "myfastapiapp_vpc_origin" {
  description = "Private CloudFront connection metadata, not a public app URL."
  value = {
    id              = aws_cloudfront_vpc_origin.myfastapiapp.id
    arn             = aws_cloudfront_vpc_origin.myfastapiapp.arn
    alb_arn         = data.aws_lb.myfastapiapp_origin.arn
    alb_domain_name = data.aws_lb.myfastapiapp_origin.dns_name
  }
}
