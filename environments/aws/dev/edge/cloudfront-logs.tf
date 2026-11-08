# CloudFront's global vended-log configuration must use us-east-1 even when its
# destination bucket is in Stockholm. This exception is only for global-service
# log delivery; the S3 bucket and every VPC resource stay in eu-north-1.
resource "aws_s3_bucket" "cloudfront_logs" {
  bucket        = "deployment-project-cf-logs-${data.aws_caller_identity.current.account_id}-eu-north-1"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "cloudfront_logs" {
  bucket                  = aws_s3_bucket.cloudfront_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "cloudfront_logs" {
  bucket = aws_s3_bucket.cloudfront_logs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudfront_logs" {
  bucket = aws_s3_bucket.cloudfront_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "cloudfront_logs" {
  bucket = aws_s3_bucket.cloudfront_logs.id
  rule {
    id     = "expire-access-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = 14
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_cloudwatch_log_delivery_source" "cloudfront" {
  region       = "us-east-1"
  name         = "deployment-project-dev-cloudfront"
  log_type     = "ACCESS_LOGS"
  resource_arn = aws_cloudfront_distribution.myfastapiapp.arn
}

resource "aws_cloudwatch_log_delivery_destination" "cloudfront" {
  region        = "us-east-1"
  name          = "deployment-project-dev-cloudfront-s3"
  output_format = "json"
  delivery_destination_configuration {
    destination_resource_arn = aws_s3_bucket.cloudfront_logs.arn
  }
}

resource "aws_s3_bucket_policy" "cloudfront_logs" {
  bucket = aws_s3_bucket.cloudfront_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.cloudfront_logs.arn, "${aws_s3_bucket.cloudfront_logs.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "AllowDeliveryAclCheck"
        Effect    = "Allow"
        Principal = { Service = "delivery.logs.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.cloudfront_logs.arn
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
          ArnEquals    = { "aws:SourceArn" = aws_cloudwatch_log_delivery_source.cloudfront.arn }
        }
      },
      {
        Sid       = "AllowDeliveryObjects"
        Effect    = "Allow"
        Principal = { Service = "delivery.logs.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.cloudfront_logs.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/CloudFront/*"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
            "s3:x-amz-acl"      = "bucket-owner-full-control"
          }
          ArnEquals = { "aws:SourceArn" = aws_cloudwatch_log_delivery_source.cloudfront.arn }
        }
      },
    ]
  })
}

resource "aws_cloudwatch_log_delivery" "cloudfront" {
  region                   = "us-east-1"
  delivery_source_name     = aws_cloudwatch_log_delivery_source.cloudfront.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.cloudfront.arn
  # No query strings, cookies, authorization headers, referer or viewer IPs.
  record_fields = [
    "date", "time", "cs-method", "cs-uri-stem", "sc-status", "sc-bytes",
    "time-taken", "x-edge-result-type", "x-edge-detailed-result-type",
    "x-edge-request-id", "ssl-protocol", "ssl-cipher",
  ]

  depends_on = [
    aws_s3_bucket_policy.cloudfront_logs,
    aws_s3_bucket_public_access_block.cloudfront_logs,
    aws_s3_bucket_ownership_controls.cloudfront_logs,
    aws_s3_bucket_server_side_encryption_configuration.cloudfront_logs,
  ]
}
