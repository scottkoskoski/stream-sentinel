# Stream-Sentinel Secrets Management Guide

## Overview

Stream-Sentinel implements comprehensive secrets management to protect sensitive configuration data such as database passwords, API keys, and authentication credentials. This guide covers setup, usage, and best practices for secure configuration management.

## 🔒 Security Model

### Development Environment
- Uses `.env` file for local configuration
- Automatic environment variable loading via `python-dotenv`
- Secure defaults with clear "CHANGEME" markers
- Git protection via `.gitignore`

### Production Environment
- Environment variables injected by orchestration platform
- Secrets management systems (Vault, K8s secrets, AWS Secrets Manager)
- SSL/TLS encryption for all service communication
- Audit logging and monitoring

## 🚀 Quick Start

### 1. Initial Setup

```bash
# Copy the example environment file
cp .env.example .env

# Generate secure passwords
python test_secrets_management.py

# Edit .env with your secure passwords
nano .env
```

### 2. Required Configuration

Replace all `CHANGEME` values in your `.env` file:

```bash
# Required: Database passwords (minimum 32 characters)
POSTGRES_PASSWORD=your-secure-postgres-password-here-min-32-chars
REDIS_PASSWORD=your-secure-redis-password-here-min-32-chars  
CLICKHOUSE_PASSWORD=your-secure-clickhouse-password-here-min-32-chars

# Required: Service authentication
KAFKA_UI_PASSWORD=your-secure-kafka-ui-password-here-min-32-chars
SCHEMA_REGISTRY_PASSWORD=your-secure-schema-registry-password-here-min-32-chars
```

### 3. Start Services

```bash
# Validate configuration
docker-compose --env-file .env config

# Start services
docker-compose --env-file .env up -d

# Verify services are running
docker-compose ps
```

## 🛠️ Configuration Files

### Environment Variables File (`.env`)

The `.env` file contains all configuration for the Stream-Sentinel system:

```bash
# Environment identifier
STREAM_SENTINEL_ENV=development

# Database credentials
POSTGRES_PASSWORD=your-secure-password
REDIS_PASSWORD=your-secure-password
CLICKHOUSE_PASSWORD=your-secure-password

# Service authentication  
KAFKA_UI_USERNAME=admin
KAFKA_UI_PASSWORD=your-secure-password

# SSL/TLS settings (for production)
SSL_ENABLED=false
SSL_CERT_PATH=/certs/server.crt
SSL_KEY_PATH=/certs/server.key
```

### Docker Compose Integration

Docker Compose services automatically load environment variables:

```yaml
services:
  postgres:
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-CHANGEME-default-password}
  
  redis:
    command: |
      sh -c '
      if [ -n "${REDIS_PASSWORD}" ]; then
        redis-server --requirepass "${REDIS_PASSWORD}"
      else
        redis-server
      fi'
```

### Python Configuration Loading

Configuration modules automatically load `.env` files:

```python
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent.parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path, override=False)

# Use environment variables with secure defaults
password = os.getenv('POSTGRES_PASSWORD', 'CHANGEME-default')
```

## 🧪 Testing and Validation

### Automated Testing

Run the secrets management validation script:

```bash
# Test configuration loading and security
python test_secrets_management.py
```

The test script validates:
- ✅ Environment variable loading from `.env` files
- ✅ Configuration system integration
- ✅ Password strength requirements
- ✅ Default password detection
- ✅ Docker Compose configuration parsing
- ✅ Git protection for sensitive files

### Manual Verification

```bash
# Test Docker Compose configuration
docker-compose --env-file .env config

# Verify environment variables are loaded
docker-compose --env-file .env exec postgres env | grep POSTGRES_PASSWORD

# Check service connectivity
docker-compose --env-file .env exec redis redis-cli ping
```

## 🔐 Password Security

### Generation

Generate secure passwords using OpenSSL:

```bash
# General service passwords (32+ characters)
openssl rand -base64 32

# SSL certificate passwords (16+ characters)  
openssl rand -base64 16

# JWT secrets (64+ characters)
openssl rand -base64 64 | tr -d '\n'

# Kafka SASL passwords (no special characters)
openssl rand -base64 32 | tr -d "=+/" | cut -c1-32
```

### Requirements

- **Minimum length**: 32 characters for service passwords
- **Complexity**: Mix of letters, numbers, and symbols
- **Uniqueness**: Different password for each service
- **Rotation**: Regular password changes in production

### Storage

- **Development**: Local `.env` file (git-ignored)
- **Production**: External secrets management system
- **Never**: Hardcoded in source code or Docker images

## 🌐 Environment-Specific Configuration

### Development

```bash
# .env.development
STREAM_SENTINEL_ENV=development
LOG_LEVEL=DEBUG
POSTGRES_PASSWORD=dev-postgres-password-not-for-production
REDIS_PASSWORD=dev-redis-password-not-for-production
SSL_ENABLED=false
```

### Staging

```bash  
# .env.staging
STREAM_SENTINEL_ENV=staging
LOG_LEVEL=INFO
POSTGRES_PASSWORD=staging-postgres-password-32-chars-minimum
REDIS_PASSWORD=staging-redis-password-32-chars-minimum
SSL_ENABLED=true
```

### Production

```bash
# Environment variables injected by orchestration platform
export STREAM_SENTINEL_ENV=production
export LOG_LEVEL=WARNING
export POSTGRES_PASSWORD=$(vault kv get -field=password secret/postgres)
export REDIS_PASSWORD=$(vault kv get -field=password secret/redis)
export SSL_ENABLED=true
```

## 🏢 Production Deployment

### Container Orchestration

#### Docker Swarm

```bash
# Store secrets in Docker Swarm
echo "production-postgres-password" | docker secret create postgres_password -
echo "production-redis-password" | docker secret create redis_password -

# Reference secrets in compose file
services:
  postgres:
    secrets:
      - postgres_password
    environment:
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
```

#### Kubernetes

```yaml
# Create secrets
apiVersion: v1
kind: Secret
metadata:
  name: stream-sentinel-secrets
data:
  postgres-password: <base64-encoded-password>
  redis-password: <base64-encoded-password>

---
# Reference in deployment
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
      - name: postgres
        env:
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: stream-sentinel-secrets
              key: postgres-password
```

### External Secrets Management

#### HashiCorp Vault

```bash
# Store secrets in Vault
vault kv put secret/stream-sentinel \
  postgres_password="production-postgres-password" \
  redis_password="production-redis-password"

# Retrieve in application
export POSTGRES_PASSWORD=$(vault kv get -field=postgres_password secret/stream-sentinel)
```

#### AWS Secrets Manager

```bash
# Create secret in AWS
aws secretsmanager create-secret \
  --name "stream-sentinel/postgres" \
  --secret-string "production-postgres-password"

# Retrieve in application
export POSTGRES_PASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id "stream-sentinel/postgres" \
  --query SecretString --output text)
```

## 🔄 Key Rotation

### Automated Rotation

```bash
#!/bin/bash
# rotate_passwords.sh

# Generate new passwords
NEW_POSTGRES_PASSWORD=$(openssl rand -base64 32)
NEW_REDIS_PASSWORD=$(openssl rand -base64 32)

# Update secrets management system
vault kv put secret/stream-sentinel \
  postgres_password="$NEW_POSTGRES_PASSWORD" \
  redis_password="$NEW_REDIS_PASSWORD"

# Rolling restart services
docker-compose --env-file .env up -d --force-recreate postgres
docker-compose --env-file .env up -d --force-recreate redis
```

### Manual Rotation

1. **Generate new password**: `openssl rand -base64 32`
2. **Update secrets store**: Vault, K8s secret, etc.
3. **Update .env file**: For development environments
4. **Restart affected services**: Rolling restart to minimize downtime
5. **Verify connectivity**: Ensure all services can authenticate
6. **Update monitoring**: Alert for authentication failures

## 📊 Monitoring and Auditing

### Security Events

Monitor for:
- Authentication failures
- Unusual access patterns
- Configuration changes
- Secret access attempts

### Logging

```python
import logging

# Security audit logger
security_logger = logging.getLogger('security.audit')
security_logger.info(f"Database connection established from {client_ip}")
security_logger.warning(f"Authentication failed for user {username}")
```

### Metrics

Track security-related metrics:
- Authentication success/failure rates
- Secret rotation compliance
- SSL certificate expiration
- Unauthorized access attempts

## ⚠️ Security Best Practices

### Development

- ✅ Use `.env` files for local development
- ✅ Never commit `.env` files to version control
- ✅ Use different passwords than production
- ✅ Keep development environment isolated

### Production

- ✅ Use external secrets management systems
- ✅ Enable SSL/TLS for all communications
- ✅ Implement proper access controls and RBAC
- ✅ Regular security assessments and penetration testing
- ✅ Monitor and audit all access
- ✅ Implement disaster recovery procedures

### General

- ✅ Use strong, unique passwords for each service
- ✅ Implement regular password rotation
- ✅ Enable multi-factor authentication
- ✅ Keep all services and dependencies updated
- ✅ Document incident response procedures
- ✅ Train team on security best practices

## 🚨 Troubleshooting

### Common Issues

#### Environment Variables Not Loading

```bash
# Check if .env file exists
ls -la .env

# Verify file contents (be careful not to expose passwords)
grep -v "PASSWORD" .env

# Test Python loading
python -c "from dotenv import load_dotenv; load_dotenv(); import os; print(os.getenv('STREAM_SENTINEL_ENV'))"
```

#### Docker Compose Configuration Errors

```bash
# Validate compose file
docker-compose --env-file .env config

# Check environment variable substitution
docker-compose --env-file .env config | grep -A5 environment

# Debug specific service
docker-compose --env-file .env up postgres
```

#### Authentication Failures

```bash
# Check PostgreSQL authentication
docker-compose --env-file .env exec postgres psql -U stream_sentinel_user -d stream_sentinel

# Check Redis authentication
docker-compose --env-file .env exec redis redis-cli AUTH $REDIS_PASSWORD ping

# Check service logs
docker-compose --env-file .env logs postgres
docker-compose --env-file .env logs redis
```

#### SSL/TLS Issues

```bash
# Verify certificate files exist and are readable
ls -la /path/to/certs/
openssl x509 -in /path/to/certs/server.crt -text -noout

# Check certificate expiration
openssl x509 -in /path/to/certs/server.crt -noout -enddate

# Test SSL connection
openssl s_client -connect localhost:5432 -servername postgres
```

### Recovery Procedures

#### Lost .env File

1. Copy from `.env.example`: `cp .env.example .env`
2. Generate new passwords: `python test_secrets_management.py`
3. Update passwords in .env file
4. Restart all services: `docker-compose restart`

#### Corrupted Configuration

1. Reset to defaults: `cp .env.example .env`
2. Restore from backup if available
3. Regenerate secrets: `openssl rand -base64 32`
4. Update external secrets stores
5. Rolling restart of services

#### Security Breach

1. **Immediate**: Rotate ALL passwords and secrets
2. **Analysis**: Check logs for unauthorized access
3. **Containment**: Isolate affected systems
4. **Recovery**: Restore from clean backups
5. **Documentation**: Document incident and lessons learned

## 📞 Support

### Getting Help

1. **Documentation**: Check this guide and related docs
2. **Testing**: Run `python test_secrets_management.py`
3. **Validation**: Use `docker-compose config` to verify configuration
4. **Logs**: Check service logs for specific error messages

### Reporting Security Issues

If you discover a security vulnerability:

1. **Do NOT** open a public issue
2. **Do NOT** commit sensitive information
3. **DO** report privately to the security team
4. **DO** include detailed reproduction steps

---

## 📚 Additional Resources

- [Docker Compose Environment Variables](https://docs.docker.com/compose/environment-variables/)
- [python-dotenv Documentation](https://python-dotenv.readthedocs.io/)
- [HashiCorp Vault](https://www.vaultproject.io/)
- [Kubernetes Secrets](https://kubernetes.io/docs/concepts/configuration/secret/)
- [OWASP Secrets Management](https://owasp.org/www-community/vulnerabilities/Use_of_hard-coded_password)

## 📋 Checklist

Before deploying to production:

- [ ] All CHANGEME passwords replaced with secure values
- [ ] Passwords meet minimum length requirements (32+ chars)
- [ ] SSL/TLS certificates generated and configured
- [ ] External secrets management system configured
- [ ] Network security and firewall rules implemented
- [ ] Monitoring and alerting configured
- [ ] Backup and disaster recovery procedures tested
- [ ] Security assessment completed
- [ ] Team trained on security procedures
- [ ] Incident response plan documented and tested