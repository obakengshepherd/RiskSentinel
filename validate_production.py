#!/usr/bin/env python3
"""
RiskSentinel Production Readiness Validator
Validates all critical components before production deployment
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime

class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

class ProductionValidator:
    def __init__(self):
        self.root_path = Path(__file__).parent
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "environment": os.getenv("APP_ENV", "development"),
            "checks": [],
            "passed": 0,
            "failed": 0,
            "warnings": 0
        }

    def print_header(self, text):
        print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*70}")
        print(f"  {text}")
        print(f"{'='*70}{Colors.RESET}\n")

    def check(self, name, condition, details=""):
        """Record a check result"""
        status = "✓ PASS" if condition else "✗ FAIL"
        color = Colors.GREEN if condition else Colors.RED
        
        print(f"{color}{status}{Colors.RESET} | {name}")
        if details:
            print(f"       └─ {details}")
        
        result = {
            "name": name,
            "passed": condition,
            "details": details,
            "timestamp": datetime.now().isoformat()
        }
        self.results["checks"].append(result)
        
        if condition:
            self.results["passed"] += 1
        else:
            self.results["failed"] += 1

    def warning(self, name, details=""):
        """Record a warning"""
        print(f"{Colors.YELLOW}⚠ WARN{Colors.RESET}  | {name}")
        if details:
            print(f"       └─ {details}")
        self.results["warnings"] += 1

    def validate_environment(self):
        """Validate environment configuration"""
        self.print_header("Environment Configuration")
        
        env = os.getenv("APP_ENV", "development")
        is_production = env == "production"
        
        self.check(
            "Environment set to production",
            is_production,
            f"Current: {env}"
        )
        
        if not is_production:
            self.warning(
                "Non-production environment detected",
                "Ensure this is intended for production deployment"
            )

    def validate_dependencies(self):
        """Validate all required Python packages"""
        self.print_header("Python Dependencies")
        
        required_packages = [
            "fastapi",
            "sqlalchemy",
            "asyncpg",
            "aiokafka",
            "pydantic",
            "python-jose",
            "passlib",
            "slowapi",
            "prometheus-client",
            "python-json-logger"
        ]
        
        for package in required_packages:
            try:
                __import__(package.replace("-", "_"))
                self.check(f"Package '{package}' installed", True)
            except ImportError:
                self.check(f"Package '{package}' installed", False)

    def validate_files(self):
        """Validate critical files exist"""
        self.print_header("Critical Files")
        
        required_files = [
            "app/main.py",
            "app/config.py",
            "app/services/security.py",
            "app/services/errors.py",
            "app/services/observability.py",
            "app/models/models.py",
            "app/models/schemas.py",
            "app/api/routes/transactions.py",
            "app/api/routes/alerts.py",
            "app/api/routes/auth.py",
            "requirements.txt",
            ".env.example",
            ".env.production",
            "Dockerfile",
            "docker-compose.yml",
            "TESTING_AND_VERIFICATION.md",
            "DEPLOYMENT.md"
        ]
        
        for file_path in required_files:
            full_path = self.root_path / file_path
            exists = full_path.exists()
            self.check(
                f"File exists: {file_path}",
                exists,
                f"Path: {full_path}"
            )

    def validate_security(self):
        """Validate security configuration"""
        self.print_header("Security Configuration")
        
        # Check environment
        auth_enabled = os.getenv("AUTH_ENABLED", "False").lower() == "true"
        self.check("Authentication enabled", auth_enabled)
        
        jwt_secret = os.getenv("JWT_SECRET_KEY")
        self.check(
            "JWT secret configured",
            bool(jwt_secret) and len(jwt_secret) >= 32,
            f"Length: {len(jwt_secret) if jwt_secret else 0} chars"
        )
        
        api_key_enabled = os.getenv("API_KEY_ENABLED", "False").lower() == "true"
        self.check("API key authentication available", api_key_enabled)
        
        rate_limit_enabled = os.getenv("RATE_LIMIT_ENABLED", "False").lower() == "true"
        self.check("Rate limiting enabled", rate_limit_enabled)
        
        # Check for debug mode
        debug_enabled = os.getenv("DEBUG", "False").lower() == "true"
        self.check("Debug mode disabled", not debug_enabled)
        if debug_enabled:
            self.warning("Debug mode is ENABLED in production environment")

    def validate_database(self):
        """Validate database configuration"""
        self.print_header("Database Configuration")
        
        db_url = os.getenv("DATABASE_URL")
        self.check(
            "Database URL configured",
            bool(db_url),
            f"Configured: {bool(db_url)}"
        )
        
        if db_url:
            is_postgres = "postgresql" in db_url
            self.check(
                "Using PostgreSQL",
                is_postgres,
                "Driver: asyncpg recommended"
            )
        
        pool_size = int(os.getenv("DATABASE_POOL_SIZE", "20"))
        self.check(
            "Database pool size configured",
            pool_size >= 10,
            f"Pool size: {pool_size}"
        )

    def validate_kafka(self):
        """Validate Kafka configuration"""
        self.print_header("Kafka Configuration")
        
        kafka_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS")
        self.check(
            "Kafka bootstrap servers configured",
            bool(kafka_servers),
            f"Servers: {kafka_servers}"
        )
        
        if kafka_servers and "," in kafka_servers:
            servers = kafka_servers.split(",")
            self.check(
                "Multiple Kafka brokers configured",
                len(servers) >= 3,
                f"Broker count: {len(servers)}"
            )

    def validate_logging(self):
        """Validate logging configuration"""
        self.print_header("Logging & Observability")
        
        log_level = os.getenv("LOG_LEVEL", "INFO")
        is_prod_appropriate = log_level in ["WARNING", "ERROR"]
        self.check(
            "Log level appropriate for production",
            is_prod_appropriate,
            f"Level: {log_level}"
        )
        
        log_format = os.getenv("LOG_FORMAT", "text")
        self.check(
            "JSON logging enabled",
            log_format == "json",
            f"Format: {log_format}"
        )
        
        structured_logging = os.getenv("STRUCTURED_LOGGING_ENABLED", "False").lower() == "true"
        self.check("Structured logging enabled", structured_logging)
        
        metrics_enabled = os.getenv("METRICS_ENABLED", "False").lower() == "true"
        self.check("Prometheus metrics enabled", metrics_enabled)

    def validate_docker(self):
        """Validate Docker configuration"""
        self.print_header("Docker Configuration")
        
        dockerfile = self.root_path / "Dockerfile"
        self.check("Dockerfile exists", dockerfile.exists())
        
        docker_compose = self.root_path / "docker-compose.yml"
        self.check("docker-compose.yml exists", docker_compose.exists())
        
        # Check if Docker is installed
        try:
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            self.check("Docker installed", result.returncode == 0, result.stdout.strip())
        except Exception as e:
            self.check("Docker installed", False, str(e))

    def validate_kubernetes(self):
        """Validate Kubernetes configuration"""
        self.print_header("Kubernetes Configuration")
        
        k8s_manifests = self.root_path / "infra" / "k8s"
        deployment_yaml = k8s_manifests / "deployment.yaml"
        
        self.check("Kubernetes manifests directory exists", k8s_manifests.exists())
        self.check("Deployment manifest exists", deployment_yaml.exists())
        
        # Check if kubectl is installed
        try:
            result = subprocess.run(
                ["kubectl", "version", "--client"],
                capture_output=True,
                text=True,
                timeout=5
            )
            self.check("kubectl installed", result.returncode == 0)
        except Exception as e:
            self.warning("kubectl not installed", "Required for K8s deployments")

    def validate_documentation(self):
        """Validate documentation completeness"""
        self.print_header("Documentation")
        
        docs = [
            ("README.md", "Main documentation"),
            ("TESTING_AND_VERIFICATION.md", "Testing guide"),
            ("DEPLOYMENT.md", "Deployment procedures"),
            (".env.example", "Configuration example"),
            (".env.production", "Production configuration"),
        ]
        
        for doc_name, description in docs:
            doc_path = self.root_path / doc_name
            self.check(
                f"{description}: {doc_name}",
                doc_path.exists()
            )

    def validate_code_quality(self):
        """Validate code quality checks"""
        self.print_header("Code Quality")
        
        # Check ruff (linter)
        try:
            result = subprocess.run(
                ["ruff", "check", "app/", "--select=E,F", "--exit-zero"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.root_path)
            )
            issues = len([l for l in result.stdout.split("\n") if l.strip()])
            self.check(
                "Ruff linting checks",
                issues == 0,
                f"Issues found: {issues}"
            )
        except FileNotFoundError:
            self.warning("Ruff not installed", "Install for code quality checks")
        except Exception as e:
            self.warning("Ruff check failed", str(e))

    def validate_tests(self):
        """Validate test suite exists"""
        self.print_header("Test Suite")
        
        tests_dir = self.root_path / "tests"
        self.check("Tests directory exists", tests_dir.exists())
        
        test_file = tests_dir / "test_risksentinel.py"
        self.check("Test file exists", test_file.exists())

    def generate_report(self):
        """Generate final report"""
        self.print_header("Production Readiness Report")
        
        total = self.results["passed"] + self.results["failed"]
        percentage = (self.results["passed"] / total * 100) if total > 0 else 0
        
        print(f"✓ Passed:  {self.results['passed']}/{total}")
        print(f"✗ Failed:  {self.results['failed']}/{total}")
        print(f"⚠ Warnings: {self.results['warnings']}")
        print(f"\n{'='*70}")
        print(f"Readiness Score: {Colors.BOLD}{percentage:.1f}%{Colors.RESET}")
        print(f"{'='*70}\n")
        
        if self.results["failed"] == 0:
            print(f"{Colors.GREEN}{Colors.BOLD}✓ System is PRODUCTION READY!{Colors.RESET}\n")
            return 0
        else:
            print(f"{Colors.RED}{Colors.BOLD}✗ Fix {self.results['failed']} issues before deploying!{Colors.RESET}\n")
            return 1

    def save_report(self):
        """Save report to JSON file"""
        report_path = self.root_path / "validation_report.json"
        with open(report_path, "w") as f:
            json.dump(self.results, f, indent=2)
        print(f"Report saved to: {report_path}")

    def run_all_checks(self):
        """Run all validation checks"""
        print(f"{Colors.BOLD}RiskSentinel Production Readiness Validator{Colors.RESET}")
        print(f"Timestamp: {datetime.now().isoformat()}\n")
        
        self.validate_environment()
        self.validate_dependencies()
        self.validate_files()
        self.validate_security()
        self.validate_database()
        self.validate_kafka()
        self.validate_logging()
        self.validate_docker()
        self.validate_kubernetes()
        self.validate_documentation()
        self.validate_code_quality()
        self.validate_tests()
        
        exit_code = self.generate_report()
        self.save_report()
        
        return exit_code

if __name__ == "__main__":
    validator = ProductionValidator()
    exit_code = validator.run_all_checks()
    sys.exit(exit_code)
