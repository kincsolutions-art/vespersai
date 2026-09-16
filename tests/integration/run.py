# ruff: noqa: E501
"""Run with Python 3.12. Creates/removes only its unique Compose project and volume."""

import json
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKS = Path(__file__).resolve().parent / "checks"
# Synthetic, non-credential value used only to probe serialization boundaries.
ADAPTER_SENTINEL = "synthetic-adapter-key-do-not-persist"
# A cold runner builds images and installs packages before the 180s readiness wait.
COLD_BUILD_TIMEOUT = 1800
# Counts the phase-3 tenant tables in the APPLICATION database (not DBOS storage).
APP_TABLE_COUNT = (
    "import os, psycopg\n"
    'c=psycopg.connect(os.environ["VESPERS_DATABASE_URL"]'
    '.replace("postgresql+psycopg:","postgresql:"))\n'
    'print(c.execute("SELECT count(*) FROM pg_tables WHERE tablename IN '
    "('users','tenants','memberships','signup_allowlist')\").fetchone()[0])\n"
)


def main() -> None:
    project = "vespers-check-" + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix=project) as directory:
        temporary = Path(directory)
        init = temporary / "init.sql"
        init.write_text((ROOT / "infra/init-db.sql").read_text().replace("_development", "_test"))
        override = temporary / "compose.yaml"
        runtime = {
            "VESPERS_ENVIRONMENT": "test",
            "VESPERS_DATABASE_URL": "postgresql+psycopg://vespers_app:local_app_only@postgres:5432/vespers_test",
            "VESPERS_DBOS_SYSTEM_DATABASE_URL": "postgresql+psycopg://vespers_dbos:local_dbos_only@postgres:5432/vespers_dbos_test",
        }
        override.write_text(
            "services:\n  postgres:\n    ports: !reset []\n    volumes: !override\n"
            "      - postgres_data:/var/lib/postgresql/data\n"
            f"      - {init}:/docker-entrypoint-initdb.d/01-init.sql:ro\n"
            "  api:\n    volumes: !reset []\n    ports: !reset []\n    environment: !override "
            + json.dumps(runtime)
            + "\n"
            "  worker:\n    volumes: !reset []\n    environment: !override "
            + json.dumps(
                runtime
                | {
                    "VESPERS_RECOVERY_PROBE_ENABLED": "true",
                    "VESPERS_ADAPTER_TEST_SECRET": ADAPTER_SENTINEL,
                }
            )
            + "\n"
            "  migrate:\n    volumes: !reset []\n    environment: !override "
            + json.dumps(
                {
                    "VESPERS_ENVIRONMENT": "test",
                    "VESPERS_MIGRATION_DATABASE_URL": "postgresql+psycopg://vespers_owner:local_owner_only@postgres:5432/vespers_test",
                }
            )
            + "\n  dashboard:\n    volumes: !reset []\n    ports: !reset []\n"
        )
        base = [
            "docker",
            "compose",
            "--project-directory",
            str(ROOT),
            "-p",
            project,
            "-f",
            str(ROOT / "compose.yaml"),
            "-f",
            str(override),
        ]

        def redact(text: str) -> str:
            return re.sub(r"(?<=://)[^@\s]+@", "[redacted]@", text).replace(
                ADAPTER_SENTINEL, "[sentinel]"
            )

        def dc(
            *args: str, code: str | None = None, check: bool = True, timeout: float = 300
        ) -> subprocess.CompletedProcess[str]:
            try:
                result = subprocess.run(
                    base + list(args), input=code, text=True, capture_output=True, timeout=timeout
                )
            except subprocess.TimeoutExpired:
                # Distinguish a harness timeout from a real check failure; `finally` still cleans up.
                raise RuntimeError(
                    f"Integration command timed out after {timeout}s: {args[:3]}"
                ) from None
            if check and result.returncode:
                # Provider secrets are never present; still avoid raw process diagnostics.
                raise RuntimeError(
                    f"Integration command failed: {args[:3]} (exit {result.returncode}): "
                    + redact(result.stderr[-2000:])
                )
            return result

        def python(service: str, code: str, *argv: str) -> str:
            return dc("exec", "-T", service, "python", "-", *argv, code=code).stdout.strip()

        def owner_sql(
            sql: str, *variables: str, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            """Administrative path: migration owner, never runtime credentials."""
            flags: list[str] = []
            for variable in variables:
                flags += ["-v", variable]
            return dc(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                *flags,
                "-U",
                "vespers_owner",
                "-d",
                "vespers_test",
                code=sql,
                check=check,
            )

        def wait_for(check: object, description: str) -> None:
            from collections.abc import Callable
            from typing import cast

            deadline = time.monotonic() + 90
            event = threading.Event()
            while time.monotonic() < deadline:
                if cast(Callable[[], bool], check)():
                    return
                event.wait(0.2)
            raise TimeoutError(description)

        try:
            dc(
                "up",
                "--build",
                "-d",
                "--wait",
                "--wait-timeout",
                "180",
                "postgres",
                "api",
                "dashboard",
                "worker",
                timeout=COLD_BUILD_TIMEOUT,
            )
            print("PASS full-stack startup", flush=True)
            for service in ("api", "worker"):
                python(
                    service,
                    "import os\n"
                    'assert "VESPERS_MIGRATION_DATABASE_URL" not in os.environ, '
                    '"migration credentials injected into a long-lived runtime service"\n',
                )
            print("PASS migration credentials absent from api and worker", flush=True)
            python(
                "api",
                """
import os, urllib.request, psycopg
for url in ["http://localhost:8000/health/live", "http://localhost:8000/health/ready", "http://dashboard:3000/", "http://dashboard:3000/api/health"]:
 assert urllib.request.urlopen(url,timeout=3).status == 200
""",
            )
            dc(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "vespers_owner",
                "-d",
                "vespers_test",
                code="""
CREATE TABLE public.privilege_probe (id integer);
INSERT INTO public.privilege_probe VALUES (1);
ALTER TABLE public.privilege_probe ENABLE ROW LEVEL SECURITY;
CREATE POLICY deny_all ON public.privilege_probe USING (false);
-- Explicit since migration 0003 removed the permissive default privileges.
-- This probe must test the RLS policy, not the absence of a grant.
GRANT SELECT ON public.privilege_probe TO vespers_app;
""",
            )
            python(
                "api",
                """
import os, psycopg
url=os.environ["VESPERS_DATABASE_URL"].replace("postgresql+psycopg:","postgresql:")
with psycopg.connect(url, autocommit=True) as c:
 assert c.execute("SELECT current_user").fetchone()[0] == "vespers_app"
 assert c.execute("SELECT count(*) FROM privilege_probe").fetchone()[0] == 0
 assert c.execute("SELECT rolsuper OR rolbypassrls OR rolinherit FROM pg_roles WHERE rolname=current_user").fetchone()[0] is False
 assert c.execute("SELECT pg_has_role(current_user,'vespers_owner','MEMBER')").fetchone()[0] is False
 assert c.execute("SELECT has_database_privilege(current_user,'vespers_dbos_test','CONNECT')").fetchone()[0] is False
 for sql in ["ALTER TABLE privilege_probe DISABLE ROW LEVEL SECURITY", "ALTER POLICY deny_all ON privilege_probe USING (true)", "SET ROLE vespers_owner", "CREATE TABLE forbidden(id integer)", "ALTER TABLE privilege_probe OWNER TO vespers_app"]:
  try: c.execute(sql)
  except psycopg.errors.InsufficientPrivilege: pass
  else: raise AssertionError("runtime privilege escalation")
""",
            )
            print("PASS actual runtime role RLS and privilege denials", flush=True)

            # ---- Tenant foundations: PostgreSQL-enforced isolation ----------
            isolation = (CHECKS / "tenant_isolation.py").read_text()
            allowlist = (ROOT / "infra/allowlist-add.sql").read_text()
            for email in ("tenant-a@synthetic.invalid", "tenant-b@synthetic.invalid"):
                owner_sql(allowlist, f"email={email}", "note=synthetic isolation test")
            print("PASS administrative allowlist provisioning (owner only)", flush=True)

            report = python("api", isolation, "provision")
            marker = [line for line in report.splitlines() if line.startswith("IDS ")]
            assert marker, report
            ids = marker[-1][4:]
            print("\n".join(x for x in report.splitlines() if not x.startswith("IDS ")), flush=True)

            # A second membership for user A in tenant B, applied out of band.
            owner_sql(
                """
INSERT INTO public.memberships (user_id, tenant_id, role)
SELECT ua.id, tb.id, 'member'
  FROM public.users ua, public.tenants tb
 WHERE ua.email_normalized = 'tenant-a@synthetic.invalid'
   AND tb.kind = 'personal'
   AND tb.owner_user_id = (
       SELECT id FROM public.users WHERE email_normalized = 'tenant-b@synthetic.invalid')
ON CONFLICT DO NOTHING;
"""
            )
            print(python("api", isolation, "shared", ids), flush=True)

            # Disable one membership, one identity, and one tenant.
            owner_sql(
                """
UPDATE public.memberships m SET status = 'disabled'
  FROM public.users u, public.tenants t
 WHERE m.user_id = u.id AND m.tenant_id = t.id
   AND u.email_normalized = 'tenant-a@synthetic.invalid' AND t.owner_user_id = u.id;
UPDATE public.tenants SET status = 'disabled'
 WHERE owner_user_id = (
   SELECT id FROM public.users WHERE email_normalized = 'tenant-b@synthetic.invalid');
UPDATE public.users SET status = 'disabled'
 WHERE email_normalized = 'tenant-b@synthetic.invalid';
"""
            )
            print(python("api", isolation, "disabled", ids), flush=True)
            print("PASS tenant isolation enforced by PostgreSQL", flush=True)

            # ---- Identity binding and opt-in grants (migration 0003) --------
            binding = (CHECKS / "identity_binding.py").read_text()
            allowlist = (ROOT / "infra/allowlist-add.sql").read_text()
            for email in (
                "alice@synthetic.invalid",
                "bob@synthetic.invalid",
                "alice.new@synthetic.invalid",
                "legacy@synthetic.invalid",
            ):
                owner_sql(allowlist, f"email={email}", "note=synthetic identity binding")
            # A table created by the migration owner AFTER 0003 must grant nothing.
            owner_sql("CREATE TABLE public.privilege_probe_new (id integer);")
            print(python("api", binding, "all"), flush=True)
            print("PASS identity binding and opt-in default grants", flush=True)

            # ---- Subject binding and revocation (migration 0004) ------------
            # Every state change below is applied with the MIGRATION OWNER, from
            # here rather than inside the api container, so the assertion that
            # api/worker never hold migration credentials still holds.
            owner_sql("INSERT INTO public.users (email) VALUES ('legacy@synthetic.invalid');")
            print(python("api", binding, "unbound"), flush=True)
            # The documented administrative procedure itself, not a hand-written
            # UPDATE: these instructions appear in the README, so they are run.
            bind = (ROOT / "infra/bind-subject.sql").read_text()
            owner_sql(bind, "email=legacy@synthetic.invalid", "subject=workos_legacy")
            print(python("api", binding, "bound"), flush=True)
            # Re-running it must refuse rather than rebind a live account.
            rebind = owner_sql(
                bind, "email=legacy@synthetic.invalid", "subject=workos_other", check=False
            )
            assert rebind.returncode != 0, rebind.stdout
            assert "refusing to rebind" in rebind.stderr, rebind.stderr
            print("  ok administrative binding refuses to rebind a live account", flush=True)
            owner_sql(
                """
DELETE FROM public.signup_allowlist WHERE email_normalized = 'legacy@synthetic.invalid';
UPDATE public.tenants SET status = 'disabled'
 WHERE owner_user_id = (
   SELECT id FROM public.users WHERE email_normalized = 'legacy@synthetic.invalid');
"""
            )
            print(python("api", binding, "revoked"), flush=True)
            print("PASS verified-subject requirement and access revocation", flush=True)
            # Reproduce the old development ownership in a disposable database.
            dc(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "postgres",
                code="CREATE DATABASE vespers_development OWNER vespers_app;",
            )
            dc(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "vespers_app",
                "-d",
                "vespers_development",
                code="CREATE TABLE preserved(id integer); INSERT INTO preserved VALUES (42);",
            )
            for _ in range(2):
                dc(
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    "postgres",
                    "-d",
                    "vespers_development",
                    code=(ROOT / "infra/transition-dev-owner.sql").read_text(),
                )
            result = dc(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-At",
                "-U",
                "vespers_app",
                "-d",
                "vespers_development",
                code="SELECT id FROM preserved; SELECT tableowner FROM pg_tables WHERE tablename='preserved';",
            )
            assert result.stdout.splitlines() == ["42", "vespers_owner"]
            print("PASS non-destructive legacy ownership transition, twice", flush=True)

            dc("run", "--rm", "worker", "python", "-m", "backend.workflows.worker", "--smoke")
            # Only DBOSClient runs here. This submitter cannot execute registered workflows.
            python(
                "api",
                """
import os, psycopg
from dbos import DBOSClient
url=os.environ["VESPERS_DBOS_SYSTEM_DATABASE_URL"]
with psycopg.connect(url.replace("postgresql+psycopg:","postgresql:")) as c:
 c.execute("CREATE SCHEMA recovery_probe")
 c.execute("CREATE TABLE recovery_probe.executions(id text, host text, pid integer)")
 c.execute("CREATE TABLE recovery_probe.control(id text PRIMARY KEY, reached boolean DEFAULT false, released boolean DEFAULT false)")
 c.execute("INSERT INTO recovery_probe.control(id) VALUES ('interrupted')")
client=DBOSClient(system_database_url=url)
client.enqueue({"workflow_name":"recovery_workflow", "queue_name":"vespers-recovery-probe", "workflow_id":"interrupted", "app_version":"scaffold-v2"}, "interrupted")
""",
            )
            query_prefix = (
                "import os, psycopg\n"
                f"SENTINEL={ADAPTER_SENTINEL!r}\n"
                'c=psycopg.connect(os.environ["VESPERS_DBOS_SYSTEM_DATABASE_URL"]'
                '.replace("postgresql+psycopg:","postgresql:"))\n'
            )
            wait_for(
                lambda: (
                    python(
                        "api",
                        query_prefix
                        + 'print(c.execute("SELECT reached FROM recovery_probe.control").fetchone()[0])',
                    )
                    == "True"
                ),
                "workflow did not reach unfinished boundary",
            )
            host = python("worker", "import socket; print(socket.gethostname())")
            actual = python(
                "api",
                query_prefix
                + 'print(c.execute("SELECT host FROM recovery_probe.executions").fetchone()[0])',
            )
            assert actual == host, "submitter executed workflow instead of resident worker"
            assert (
                python(
                    "api",
                    query_prefix
                    + "print(c.execute(\"SELECT status FROM dbos.workflow_status WHERE workflow_uuid='interrupted'\").fetchone()[0])",
                )
                == "PENDING"
            )
            dc("kill", "-s", "SIGKILL", "worker")
            assert (
                dc(
                    "exec",
                    "-T",
                    "worker",
                    "python",
                    "-m",
                    "backend.workflows.readiness",
                    check=False,
                ).returncode
                != 0
            )
            python(
                "api",
                query_prefix
                + 'c.execute("UPDATE recovery_probe.control SET released=true"); c.commit()',
            )
            dc("start", "worker")
            wait_for(
                lambda: (
                    dc(
                        "exec",
                        "-T",
                        "worker",
                        "python",
                        "-m",
                        "backend.workflows.readiness",
                        check=False,
                    ).returncode
                    == 0
                ),
                "resident worker did not become ready",
            )
            wait_for(
                lambda: (
                    python(
                        "api",
                        query_prefix
                        + "print(c.execute(\"SELECT status FROM dbos.workflow_status WHERE workflow_uuid='interrupted'\").fetchone()[0])",
                    )
                    == "SUCCESS"
                ),
                "interrupted workflow did not recover",
            )
            python(
                "api",
                query_prefix
                + """
assert c.execute("SELECT count(*) FROM recovery_probe.executions").fetchone()[0] == 1
from dbos import DBOSClient
client=DBOSClient(system_database_url=os.environ["VESPERS_DBOS_SYSTEM_DATABASE_URL"])
assert client.retrieve_workflow("interrupted").get_result() == "recovered"
""",
            )
            print("PASS resident interrupted recovery; completed step executed once", flush=True)
            python(
                "api",
                query_prefix
                + """
c.execute("INSERT INTO recovery_probe.control(id) VALUES ('adapter')"); c.commit()
from dbos import DBOSClient
client=DBOSClient(system_database_url=os.environ["VESPERS_DBOS_SYSTEM_DATABASE_URL"])
client.enqueue({"workflow_name":"adapter_workflow", "queue_name":"vespers-recovery-probe", "workflow_id":"adapter", "app_version":"scaffold-v2"})
""",
            )
            wait_for(
                lambda: (
                    python(
                        "api",
                        query_prefix
                        + "print(c.execute(\"SELECT reached FROM recovery_probe.control WHERE id='adapter'\").fetchone()[0])",
                    )
                    == "True"
                ),
                "adapter boundary not reached",
            )
            python(
                "api",
                query_prefix
                + """
assert c.execute("SELECT status FROM dbos.workflow_status WHERE workflow_uuid='adapter'").fetchone()[0] == "PENDING"
assert c.execute("SELECT count(*) FROM recovery_probe.executions WHERE id='adapter:tool'").fetchone()[0] == 1
""",
            )
            dc("kill", "-s", "SIGKILL", "worker")
            python(
                "api",
                query_prefix
                + "c.execute(\"UPDATE recovery_probe.control SET released=true WHERE id='adapter'\"); c.commit()",
            )
            dc("start", "worker")
            wait_for(
                lambda: (
                    python(
                        "api",
                        query_prefix
                        + "print(c.execute(\"SELECT status FROM dbos.workflow_status WHERE workflow_uuid='adapter'\").fetchone()[0])",
                    )
                    == "SUCCESS"
                ),
                "adapter did not recover",
            )
            counts = python(
                "api",
                query_prefix
                + """
import json, base64
rows=c.execute("SELECT id,count(*) FROM recovery_probe.executions WHERE id LIKE 'adapter:%' GROUP BY id ORDER BY id").fetchall()
assert dict(rows) == {'adapter:model-first':1,'adapter:model-final':2,'adapter:tool':1}, rows
assert c.execute("SELECT count(DISTINCT host) FROM recovery_probe.executions").fetchone()[0] == 1
from dbos import DBOSClient
client=DBOSClient(system_database_url=os.environ["VESPERS_DBOS_SYSTEM_DATABASE_URL"])
assert client.retrieve_workflow('adapter').get_result() == '42'
# Inspect serialized storage without deserializing pickle into executable objects.
secret=SENTINEL.encode()
for table in ['workflow_status','operation_outputs']:
 for (record,) in c.execute('SELECT row_to_json(t)::text FROM dbos.'+table+' t'):
  assert secret not in record.encode()
  for value in json.loads(record).values():
   if isinstance(value,str):
    try: decoded=base64.b64decode(value,validate=True)
    except ValueError: continue
    assert secret not in decoded
print(json.dumps(dict(rows)))
""",
            )
            log_result = dc("logs", "worker")
            logs = log_result.stdout + log_result.stderr
            assert ADAPTER_SENTINEL not in logs
            print(
                "PASS Pydantic AI adapter recovery and scoped serialization: " + counts, flush=True
            )
            dc("stop", "postgres")
            assert (
                dc(
                    "exec",
                    "-T",
                    "worker",
                    "python",
                    "-m",
                    "backend.workflows.readiness",
                    check=False,
                ).returncode
                != 0
            )
            python(
                "api",
                """
import urllib.request, urllib.error
assert urllib.request.urlopen("http://localhost:8000/health/live",timeout=3).status == 200
try: urllib.request.urlopen("http://localhost:8000/health/ready",timeout=3)
except urllib.error.HTTPError as e: assert e.code == 503
else: raise AssertionError("readiness ignored outage")
""",
            )
            dc("start", "postgres")
            wait_for(
                lambda: (
                    dc(
                        "exec",
                        "-T",
                        "worker",
                        "python",
                        "-m",
                        "backend.workflows.readiness",
                        check=False,
                    ).returncode
                    == 0
                ),
                "readiness did not recover after outage",
            )
            print("PASS negative readiness, outage and reconnection", flush=True)

            dc("run", "--rm", "migrate", "alembic", "downgrade", "0001")
            assert (
                python(
                    "api",
                    APP_TABLE_COUNT,
                )
                == "0"
            )
            dc("run", "--rm", "migrate", "alembic", "upgrade", "head")
            assert (
                python(
                    "api",
                    APP_TABLE_COUNT,
                )
                == "4"
            )
            print("PASS baseline-to-head migration round trip on disposable storage", flush=True)
        finally:
            dc("down", "-v", "--remove-orphans")
            print("Removed disposable project " + project, flush=True)


if __name__ == "__main__":
    main()
