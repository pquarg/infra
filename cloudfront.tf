data "aws_cloudfront_cache_policy" "api_disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "api_all_viewer" {
  name = "Managed-AllViewer"
}

resource "aws_cloudfront_function" "https_redirects" {
  name    = "deployment-project-dev-https-redirects"
  runtime = "cloudfront-js-2.0"
  comment = "Preserve HTTPS in same-host redirects from the private HTTP origin"
  publish = true
  code    = file("${path.module}/functions/https-redirects.js")
}

resource "aws_cloudfront_response_headers_policy" "api" {
  name = "deployment-project-dev-myfastapiapp"

  security_headers_config {
    content_security_policy {
      # Swagger uses jsDelivr and inline bootstrap code; its favicon is hosted by FastAPI.
      content_security_policy = "default-src 'none'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: https://fastapi.tiangolo.com; connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
      override                = true
    }
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "no-referrer"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = false
      preload                    = false
      override                   = true
    }
  }

  custom_headers_config {
    items {
      header   = "Cache-Control"
      value    = "no-store"
      override = true
    }
  }
}

resource "aws_cloudfront_distribution" "myfastapiapp" {
  comment             = "Learning FastAPI application through a private Stockholm ALB"
  enabled             = true
  is_ipv6_enabled     = true
  http_version        = "http2and3"
  price_class         = "PriceClass_100"
  wait_for_deployment = true
  retain_on_delete    = false

  origin {
    domain_name = data.aws_lb.myfastapiapp_origin.dns_name
    origin_id   = "myfastapiapp-private-alb"

    vpc_origin_config {
      vpc_origin_id            = aws_cloudfront_vpc_origin.myfastapiapp.id
      origin_keepalive_timeout = 5
      origin_read_timeout      = 30
    }
  }

  default_cache_behavior {
    target_origin_id           = "myfastapiapp-private-alb"
    viewer_protocol_policy     = "https-only"
    allowed_methods            = ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = data.aws_cloudfront_cache_policy.api_disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.api_all_viewer.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.api.id
    compress                   = true

    function_association {
      event_type   = "viewer-response"
      function_arn = aws_cloudfront_function.https_redirects.arn
    }
  }

  # Auth errors (401) are not custom-error-cacheable. Zero every supported error
  # TTL as well as the cache policy; never replace API errors with an HTML page.
  dynamic "custom_error_response" {
    for_each = toset([400, 403, 404, 405, 414, 416, 500, 501, 502, 503, 504])
    content {
      error_code            = custom_error_response.value
      error_caching_min_ttl = 0
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # AWS supplies the certificate for the assigned *.cloudfront.net hostname.
  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

output "myfastapiapp_https" {
  description = "Public HTTPS endpoint; verify health, auth, redirects and log delivery after apply."
  value = {
    distribution_id = aws_cloudfront_distribution.myfastapiapp.id
    domain_name     = aws_cloudfront_distribution.myfastapiapp.domain_name
    url             = "https://${aws_cloudfront_distribution.myfastapiapp.domain_name}"
    docs_url        = "https://${aws_cloudfront_distribution.myfastapiapp.domain_name}/api/v1/docs/"
    logs_bucket     = aws_s3_bucket.cloudfront_logs.bucket
  }
}
