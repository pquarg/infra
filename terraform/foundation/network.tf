module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "6.7.3"

  name = "deployment-project-dev"
  cidr = "10.42.0.0/16"

  azs = ["eu-north-1a", "eu-north-1b"]

  public_subnets = [
    "10.42.0.0/24",
    "10.42.1.0/24",
  ]

  private_subnets = [
    "10.42.16.0/20",
    "10.42.32.0/20",
  ]

  database_subnets = [
    "10.42.48.0/24",
    "10.42.49.0/24",
  ]

  enable_dns_hostnames = true
  enable_dns_support   = true

  enable_nat_gateway     = true
  single_nat_gateway     = true
  one_nat_gateway_per_az = false

  create_database_subnet_group           = false
  create_database_subnet_route_table     = true
  create_database_internet_gateway_route = false
  create_database_nat_gateway_route      = false

  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
}

output "network" {
  description = "Network IDs for the cluster and database."
  value = {
    vpc_id              = module.vpc.vpc_id
    public_subnet_ids   = module.vpc.public_subnets
    private_subnet_ids  = module.vpc.private_subnets
    database_subnet_ids = module.vpc.database_subnets
  }
}
