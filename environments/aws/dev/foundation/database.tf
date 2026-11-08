resource "aws_db_subnet_group" "postgres" {
  name        = "deployment-project-dev-postgres"
  description = "Isolated PostgreSQL subnets across two Availability Zones"
  subnet_ids  = module.vpc.database_subnets
}

resource "aws_security_group" "postgres" {
  name        = "deployment-project-dev-postgres"
  description = "PostgreSQL access from the EKS workers only"
  vpc_id      = module.vpc.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "postgres_from_workers" {
  security_group_id            = aws_security_group.postgres.id
  referenced_security_group_id = module.eks.node_security_group_id
  description                  = "PostgreSQL from EKS workers"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_db_parameter_group" "postgres" {
  name        = "deployment-project-dev-postgres17"
  family      = "postgres17"
  description = "PostgreSQL 17 with TLS required"

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }
}

resource "aws_db_instance" "postgres" {
  identifier     = "deployment-project-dev-postgres"
  engine         = "postgres"
  engine_version = "17.11"
  instance_class = "db.t4g.micro"

  db_name  = "myfastapiapp"
  username = "dbadmin"
  port     = 5432

  # AWS generates, stores and rotates the administrator password.
  # The application will get a separate, less privileged database login.
  manage_master_user_password = true

  allocated_storage     = 20
  max_allocated_storage = 40
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.postgres.name
  parameter_group_name   = aws_db_parameter_group.postgres.name
  vpc_security_group_ids = [aws_security_group.postgres.id]
  publicly_accessible    = false
  multi_az               = false

  # Use a one-day recovery window for this Free-plan learning environment.
  # The first create attempt with seven days was rejected by RDS.
  backup_retention_period = 1
  backup_window           = "02:00-03:00"
  maintenance_window      = "sun:03:30-sun:04:30"
  copy_tags_to_snapshot   = true

  # Keep the lab reproducible; review minor upgrades through Terraform.
  auto_minor_version_upgrade  = false
  allow_major_version_upgrade = false

  # Restore deletion safeguards for a future rebuild; the prior lab is destroyed.
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "deployment-project-dev-postgres-final"
  delete_automated_backups  = false

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [aws_vpc_security_group_ingress_rule.postgres_from_workers]
}

output "database" {
  description = "Private database connection details and administrator secret reference."
  value = {
    identifier        = aws_db_instance.postgres.identifier
    address           = aws_db_instance.postgres.address
    port              = aws_db_instance.postgres.port
    name              = aws_db_instance.postgres.db_name
    master_username   = aws_db_instance.postgres.username
    master_secret_arn = aws_db_instance.postgres.master_user_secret[0].secret_arn
  }
}
