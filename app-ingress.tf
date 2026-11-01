# First public-access batch: prepare the frontend security group for an internal
# controller-managed ALB. CloudFront VPC origin/distribution follow after the ALB
# is Active. Do not manage the ALB or its target groups in Terraform.
data "aws_ec2_managed_prefix_list" "cloudfront_origin_facing" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "myfastapiapp_alb" {
  name        = "deployment-project-dev-myfastapiapp-alb"
  description = "Internal application ALB reached through CloudFront VPC origins"
  vpc_id      = module.vpc.vpc_id

  # LBC resolves a security-groups annotation name by this Name tag.
  tags = {
    Name = "deployment-project-dev-myfastapiapp-alb"
  }
}

resource "aws_vpc_security_group_ingress_rule" "myfastapiapp_from_cloudfront" {
  security_group_id = aws_security_group.myfastapiapp_alb.id
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront_origin_facing.id
  description       = "HTTP over the private CloudFront VPC origin connection"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "myfastapiapp_to_workers" {
  security_group_id            = aws_security_group.myfastapiapp_alb.id
  referenced_security_group_id = module.eks.node_security_group_id
  description                  = "Application traffic and readiness checks to pod port 8000"
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

output "myfastapiapp_alb_security_group" {
  description = "Frontend SG for the internal ALB; the controller owns its backend rules."
  value = {
    id   = aws_security_group.myfastapiapp_alb.id
    name = aws_security_group.myfastapiapp_alb.tags["Name"]
  }
}
