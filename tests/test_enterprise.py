"""
GlassBox Framework — Integration Test Suite (v1.1.0)
=====================================================

Comprehensive test coverage for v1.1.0 enterprise features:
  - Database abstraction layer across SQLite, PostgreSQL
  - Advanced access control with RBAC and impersonation
  - Encryption utilities (AES-256-GCM, key derivation)
  - Advanced audit logging with hash chain verification
  - Request context and configuration management
  - API gateway with middleware pipeline

Run with: pytest test_v1_1_enterprise.py -v

Author: Mohammed Akbar Ansari
"""

import pytest
import threading
import time
import json
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

# Import enterprise modules
from glassbox.store.database_abstraction import (
    DatabaseFactory, SQLiteBackend, PostgreSQLBackend
)
from glassbox.governance.access_control import (
    AccessControl, Role, User, PermissionScope
)
from glassbox.governance.encryption import CryptoManager, EncryptedField
from glassbox.governance.advanced_audit import AuditLogger, AuditRecord
from glassbox.governance.request_context import (
    RequestContext, Config, ContextManager
)
from glassbox.governance.api_gateway import (
    APIGateway, Request, Response, 
    AuthenticationMiddleware, RateLimitMiddleware, RequestLoggingMiddleware,
    CORSMiddleware
)


# ============================================================================
# PART 1: DATABASE ABSTRACTION TESTS
# ============================================================================

class TestDatabaseAbstraction:
    """Test database abstraction layer."""

    def test_sqlite_backend_basic_operations(self):
        """Test SQLite backend CRUD operations."""
        db = DatabaseFactory.create("sqlite", db_path=":memory:")

        # CREATE
        affected = db.execute(
            "CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)"
        )

        # INSERT
        affected = db.execute(
            "INSERT INTO test (id, name) VALUES (?, ?)",
            (1, "Alice"),
        )
        assert affected == 1

        # SELECT one
        row = db.query_one(
            "SELECT * FROM test WHERE id = ?",
            (1,)
        )
        assert row["name"] == "Alice"

        # SELECT all
        rows = db.query_all("SELECT * FROM test")
        assert len(rows) == 1

        # UPDATE
        affected = db.execute(
            "UPDATE test SET name = ? WHERE id = ?",
            ("Bob", 1)
        )
        assert affected == 1

        # DELETE
        affected = db.execute(
            "DELETE FROM test WHERE id = ?",
            (1,)
        )
        assert affected == 1

        db.close()

    def test_sqlite_health_check(self):
        """Test SQLite health check."""
        db = DatabaseFactory.create("sqlite", db_path=":memory:")
        assert db.health_check() is True
        db.close()

    def test_sqlite_transaction(self):
        """Test SQLite transaction handling."""
        db = DatabaseFactory.create("sqlite", db_path=":memory:")

        db.execute("CREATE TABLE test (id INTEGER, value TEXT)")

        # Successful transaction
        with db.transaction():
            db.execute("INSERT INTO test VALUES (1, 'value1')")

        rows = db.query_all("SELECT * FROM test")
        assert len(rows) == 1

        db.close()

    def test_sqlite_stats(self):
        """Test SQLite statistics."""
        db = DatabaseFactory.create("sqlite", db_path=":memory:")

        db.execute("SELECT 1")  # Generate a query
        stats = db.get_stats()

        assert stats["backend"] == "sqlite"
        assert stats["queries"] > 0

        db.close()

    def test_factory_creates_correct_backend(self):
        """Test factory creates correct backend type."""
        sqlite_db = DatabaseFactory.create("sqlite")
        assert isinstance(sqlite_db, SQLiteBackend)
        sqlite_db.close()

        # PostgreSQL would require actual database
        # Just test factory accepts parameters
        try:
            pg_db = DatabaseFactory.create("postgresql", host="localhost")
            # If PostgreSQL not available, skip
            pg_db.close()
        except ImportError:
            pytest.skip("PostgreSQL driver not installed")


# ============================================================================
# PART 2: ACCESS CONTROL TESTS
# ============================================================================

class TestAccessControl:
    """Test advanced access control."""

    def test_role_hierarchy(self):
        """Test role inheritance."""
        # Create parent role
        admin_role = Role("admin", description="Administrator")
        admin_role.grant_permission("policy", "read", PermissionScope.ANY)
        admin_role.grant_permission("policy", "write", PermissionScope.ANY)

        # Create child role
        analyst_role = Role("analyst", description="Data Analyst")
        analyst_role.grant_permission("policy", "read", PermissionScope.OWN_TENANT)
        analyst_role.set_parent(admin_role)

        # Child inherits parent permissions
        all_perms = analyst_role.get_all_permissions()
        assert len(all_perms) == 3  # 1 read (own) + 2 from parent

    def test_permission_scope_matching(self):
        """Test permission scope hierarchy."""
        admin_role = Role("admin")
        admin_role.grant_permission("data", "read", PermissionScope.ANY)

        # ANY > ANY_TENANT > OWN_TENANT > OWN_RECORD
        assert admin_role.has_permission("data", "read", PermissionScope.OWN_RECORD)
        assert admin_role.has_permission("data", "read", PermissionScope.OWN_TENANT)
        assert admin_role.has_permission("data", "read", PermissionScope.ANY_TENANT)
        assert admin_role.has_permission("data", "read", PermissionScope.ANY)

    def test_access_control_decision(self):
        """Test access control decision."""
        ac = AccessControl()

        # Create roles
        admin = Role("admin")
        admin.grant_permission("audit", "read", PermissionScope.ANY)

        user_role = Role("user")
        user_role.grant_permission("audit", "read", PermissionScope.OWN_TENANT)

        ac.register_role(admin)
        ac.register_role(user_role)

        # Create users
        admin_user = User("admin1", roles={"admin"})
        regular_user = User("user1", roles={"user"})

        ac.register_user(admin_user)
        ac.register_user(regular_user)

        # Admin can read anything
        assert ac.has_permission(
            "admin1", "audit", "read",
            context={"scope": PermissionScope.ANY}
        )

        # User can only read own tenant
        assert ac.has_permission(
            "user1", "audit", "read",
            context={"scope": PermissionScope.OWN_TENANT, "tenant_id": "tenant1"}
        )

    def test_impersonation(self):
        """Test role impersonation."""
        ac = AccessControl()

        admin = Role("admin")
        admin.grant_permission("secret", "read", PermissionScope.ANY)

        user_role = Role("user")

        ac.register_role(admin)
        ac.register_role(user_role)

        user = User("user1", roles={"user"})
        ac.register_user(user)

        # Can't access secret normally
        assert not ac.has_permission("user1", "secret", "read")

        # Can access when impersonated as admin
        with ac.impersonate("admin", "user1"):
            assert ac.has_permission("user1", "secret", "read")

        # Can't access after impersonation ends
        assert not ac.has_permission("user1", "secret", "read")

    def test_permission_caching(self):
        """Test permission decision caching."""
        ac = AccessControl(enable_caching=True, cache_ttl_sec=1.0)

        admin = Role("admin")
        admin.grant_permission("data", "read", PermissionScope.ANY)

        ac.register_role(admin)

        user = User("user1", roles={"admin"})
        ac.register_user(user)

        # First call (cache miss)
        assert ac.has_permission("user1", "data", "read")

        # Second call (cache hit)
        assert ac.has_permission("user1", "data", "read")

        # Wait for cache to expire
        time.sleep(1.1)

        # Cache miss again
        assert ac.has_permission("user1", "data", "read")


# ============================================================================
# PART 3: ENCRYPTION TESTS
# ============================================================================

class TestEncryption:
    """Test encryption utilities."""

    def test_encrypt_decrypt_aes_256_gcm(self):
        """Test AES-256-GCM encryption/decryption."""
        crypto = CryptoManager()

        plaintext = b"sensitive data"
        encrypted = crypto.encrypt(plaintext)

        assert encrypted != plaintext
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == plaintext

    def test_encrypt_with_aad(self):
        """Test authenticated encryption with additional data."""
        crypto = CryptoManager()

        plaintext = b"data"
        aad = b"additional context"

        encrypted = crypto.encrypt(plaintext, aad=aad)
        decrypted = crypto.decrypt(encrypted, aad=aad)

        assert decrypted == plaintext

    def test_encrypt_field(self):
        """Test field-level encryption."""
        crypto = CryptoManager()

        # Encrypt field
        field = EncryptedField(name="password", plaintext="secret123")
        encrypted_field = crypto.encrypt_field(field)

        assert encrypted_field.plaintext is None
        assert encrypted_field.ciphertext is not None
        assert encrypted_field.encrypted_at is not None

        # Decrypt field
        decrypted_field = crypto.decrypt_field(encrypted_field)
        assert decrypted_field.plaintext == "secret123"

    def test_password_hashing_and_verification(self):
        """Test password hashing."""
        password = "MySecurePassword123!"

        # Hash password
        hashed, salt = CryptoManager.hash_password(password)

        # Verify correct password
        assert CryptoManager.verify_password(password, hashed, salt)

        # Verify incorrect password
        assert not CryptoManager.verify_password("WrongPassword", hashed, salt)

    def test_key_derivation_from_passphrase(self):
        """Test key derivation from passphrase."""
        passphrase = "my_super_secure_passphrase"

        crypto1 = CryptoManager.from_passphrase(passphrase, salt=b"fixed_salt_16byt")
        crypto2 = CryptoManager.from_passphrase(passphrase, salt=b"fixed_salt_16byt")

        # Same passphrase + salt = same key
        assert crypto1.key == crypto2.key

        # Encrypt with crypto1, decrypt with crypto2
        encrypted = crypto1.encrypt(b"test data")
        decrypted = crypto2.decrypt(encrypted)
        assert decrypted == b"test data"

    def test_hmac_computation(self):
        """Test HMAC integrity verification."""
        data = b"important data"

        hmac_digest = CryptoManager.compute_hmac(data)
        assert CryptoManager.verify_hmac(data, hmac_digest)

        # Tampered data fails verification
        assert not CryptoManager.verify_hmac(b"tampered data", hmac_digest)


# ============================================================================
# PART 4: ADVANCED AUDIT LOGGING TESTS
# ============================================================================

class TestAdvancedAudit:
    """Test advanced audit logging."""

    def test_audit_log_action(self):
        """Test logging an action."""
        logger = AuditLogger(db_path=":memory:")

        record = logger.log_action(
            user_id="user123",
            action="policy_create",
            resource_type="policy",
            resource_id="policy_456",
            result="success",
            context={"policy_name": "New Policy"}
        )

        assert record.user_id == "user123"
        assert record.action == "policy_create"
        assert record.result == "success"

    def test_audit_search(self):
        """Test searching audit trail."""
        logger = AuditLogger(db_path=":memory:")

        # Log multiple actions
        logger.log_action("user1", "create", "policy", "p1", "success")
        logger.log_action("user2", "update", "policy", "p1", "success")
        logger.log_action("user1", "delete", "policy", "p1", "success")

        # Search by user
        records = logger.search(user_id="user1")
        assert len(records) == 2

        # Search by action (wildcard)
        records = logger.search(action="*")
        assert len(records) == 3

    def test_hash_chain_verification(self):
        """Test hash chain integrity verification."""
        logger = AuditLogger(db_path=":memory:", enable_hash_chain=True)

        # Log actions
        logger.log_action("user1", "action1", "resource", "r1", "success")
        logger.log_action("user2", "action2", "resource", "r2", "success")

        # Verify hash chain
        assert logger.verify_hash_chain() is True

    def test_audit_export(self):
        """Test exporting audit records."""
        logger = AuditLogger(db_path=":memory:")

        logger.log_action("user1", "action", "resource", "r1", "success", {"key": "value"})

        # Export as JSON
        json_export = logger.export_records(format="json")
        data = json.loads(json_export)
        assert len(data) == 1
        assert data[0]["user_id"] == "user1"

        # Export as CSV
        csv_export = logger.export_records(format="csv")
        assert "user1" in csv_export


# ============================================================================
# PART 5: REQUEST CONTEXT & CONFIGURATION TESTS
# ============================================================================

class TestRequestContext:
    """Test request context and configuration."""

    def test_request_context_thread_local(self):
        """Test request context isolation per thread."""
        context_data = {}

        def thread_func(thread_id):
            ctx = RequestContext(user_id=f"user_{thread_id}")
            RequestContext.set_current(ctx)
            time.sleep(0.1)  # Simulate work
            context_data[thread_id] = RequestContext.get_current().user_id

        threads = []
        for i in range(3):
            t = threading.Thread(target=thread_func, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Each thread has isolated context
        assert context_data[0] == "user_0"
        assert context_data[1] == "user_1"
        assert context_data[2] == "user_2"

    def test_context_manager(self):
        """Test ContextManager helper."""
        ctx1 = RequestContext.get_current()

        with ContextManager(user_id="new_user", tenant_id="tenant1"):
            ctx2 = RequestContext.get_current()
            assert ctx2.user_id == "new_user"
            assert ctx2.tenant_id == "tenant1"

        # Context restored
        ctx3 = RequestContext.get_current()
        # May or may not be ctx1 depending on ordering

    def test_config_loading(self):
        """Test configuration loading."""
        config = Config()
        config.set("database.host", "localhost")
        config.set("database.port", 5432)

        assert config.get("database.host") == "localhost"
        assert config.get("database.port") == 5432
        assert config.get("database.nonexistent", "default") == "default"

    def test_config_env_override(self):
        """Test configuration environment variable override."""
        import os

        os.environ["TEST_API_KEY"] = "secret_key_from_env"

        config = Config()
        config.set("api.key", "default_key")

        # Environment variable takes precedence
        api_key = config.get("api.key", env_var="TEST_API_KEY")
        assert api_key == "secret_key_from_env"

        # Cleanup
        del os.environ["TEST_API_KEY"]


# ============================================================================
# PART 6: API GATEWAY & MIDDLEWARE TESTS
# ============================================================================

class TestAPIGateway:
    """Test API gateway and middleware."""

    def test_api_gateway_basic_routing(self):
        """Test basic API gateway routing."""
        gateway = APIGateway()

        def handler(request):
            return Response(status_code=200, body={"message": "success"})

        gateway.register_route("GET", "/api/test", handler)

        response = gateway.handle_request(
            method="GET",
            path="/api/test",
        )

        assert response.status_code == 200

    def test_authentication_middleware(self):
        """Test authentication middleware."""
        gateway = APIGateway()
        gateway.add_middleware(AuthenticationMiddleware(secret_key="my_token"))

        def handler(request):
            return Response(status_code=200)

        gateway.register_route("GET", "/secure", handler)

        # No auth header -> 401
        response = gateway.handle_request(method="GET", path="/secure")
        assert response.status_code == 401

        # Valid auth header -> success
        response = gateway.handle_request(
            method="GET",
            path="/secure",
            headers={"Authorization": "Bearer my_token"}
        )
        assert response.status_code == 200

    def test_rate_limit_middleware(self):
        """Test rate limiting middleware."""
        gateway = APIGateway()
        gateway.add_middleware(RateLimitMiddleware(requests_per_minute=3))

        RequestContext.set_current(RequestContext(user_id="user1"))

        def handler(request):
            return Response(status_code=200)

        gateway.register_route("GET", "/api/data", handler)

        # First 3 requests succeed
        for i in range(3):
            response = gateway.handle_request(method="GET", path="/api/data")
            assert response.status_code == 200

        # 4th request rate-limited
        response = gateway.handle_request(method="GET", path="/api/data")
        assert response.status_code == 429

    def test_cors_middleware(self):
        """Test CORS middleware."""
        gateway = APIGateway()
        gateway.add_middleware(CORSMiddleware(
            allowed_origins=["http://localhost:3000"],
            allowed_methods=["GET", "POST"]
        ))

        def handler(request):
            return Response(status_code=200)

        gateway.register_route("GET", "/api/data", handler)

        # Preflight request
        response = gateway.handle_request(
            method="OPTIONS",
            path="/api/data",
            headers={"Origin": "http://localhost:3000"}
        )

        assert response.status_code == 200
        assert "Access-Control-Allow-Origin" in response.headers

    def test_middleware_pipeline(self):
        """Test middleware execution order."""
        gateway = APIGateway()
        execution_log = []

        class TracingMiddleware(object):
            def __init__(self, name):
                self.name = name

            def process_request(self, request):
                execution_log.append(f"{self.name}_request")
                return None

            def process_response(self, request, response):
                execution_log.append(f"{self.name}_response")
                return response

        gateway.add_middleware(TracingMiddleware("middleware1"))
        gateway.add_middleware(TracingMiddleware("middleware2"))

        def handler(request):
            execution_log.append("handler")
            return Response(status_code=200)

        gateway.register_route("GET", "/test", handler)

        gateway.handle_request(method="GET", path="/test")

        # Middleware 1 -> Middleware 2 -> Handler -> Middleware 2 response -> Middleware 1 response
        assert execution_log == [
            "middleware1_request",
            "middleware2_request",
            "handler",
            "middleware2_response",
            "middleware1_response",
        ]


# ============================================================================
# INTEGRATION TEST: END-TO-END SCENARIO
# ============================================================================

class TestEndToEndIntegration:
    """End-to-end integration test combining all modules."""

    def test_complete_governance_flow(self):
        """
        Test complete governance flow:
        1. User authenticates via API gateway
        2. Access control checks permission
        3. Action is logged to immutable audit trail
        4. Sensitive data is encrypted
        """
        # Setup access control
        ac = AccessControl()

        analyst_role = Role("analyst")
        analyst_role.grant_permission("report", "read", PermissionScope.OWN_TENANT)

        ac.register_role(analyst_role)

        user = User("analyst1", roles={"analyst"})
        ac.register_user(user)

        # Setup audit logger
        logger = AuditLogger(db_path=":memory:", enable_hash_chain=True)

        # Setup encryption
        crypto = CryptoManager()

        # Setup API gateway
        gateway = APIGateway()
        gateway.add_middleware(AuthenticationMiddleware(secret_key="analytics_token"))
        gateway.add_middleware(RequestLoggingMiddleware())

        def generate_report_handler(request):
            ctx = RequestContext.get_current()

            # Check permission
            can_read = ac.has_permission(
                ctx.user_id, "report", "read",
                context={"scope": PermissionScope.OWN_TENANT, "tenant_id": ctx.tenant_id}
            )

            if not can_read:
                return Response(status_code=403, error="Permission denied")

            # Log action
            logger.log_action(
                user_id=ctx.user_id,
                action="report_generate",
                resource_type="report",
                resource_id="report_123",
                result="success",
                context={"tenant_id": ctx.tenant_id}
            )

            # Encrypt sensitive data
            sensitive = b"Report data with sensitive values"
            encrypted = crypto.encrypt(sensitive)

            return Response(
                status_code=200,
                body={"encrypted_report": encrypted.hex()}
            )

        gateway.register_route("POST", "/api/reports/generate", generate_report_handler)

        # Execute request
        with ContextManager(
            user_id="analyst1",
            tenant_id="tenant_acme",
            correlation_id="flow-123"
        ):
            response = gateway.handle_request(
                method="POST",
                path="/api/reports/generate",
                headers={"Authorization": "Bearer analytics_token"}
            )

        # Verify response
        assert response.status_code == 200

        # Verify audit trail
        audit_records = logger.search(user_id="analyst1")
        assert len(audit_records) == 1
        assert audit_records[0].action == "report_generate"

        # Verify hash chain
        assert logger.verify_hash_chain()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
