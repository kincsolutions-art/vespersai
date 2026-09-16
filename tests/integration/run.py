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
                    "VESPERS_ADAPTER_TEST_SECRET": "synthetic-adapter-key-do-not-persist",
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

        def dc(
            *args: str, code: str | None = None, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            result = subprocess.run(
                base + list(args), input=code, text=True, capture_output=True, timeout=300
            )
            if check and result.returncode:
                # Provider secrets are never present; still avoid raw process diagnostics.
                raise RuntimeError(
                    f"Integration command failed: {args[:3]} (exit {result.returncode}): "
                    + re.sub(r"(?<=://)[^@\s]+@", "[redacted]@", result.stderr[-2000:]).replace(
                        "synthetic-adapter-key-do-not-persist", "[sentinel]"
                    )
                )
            return result

        def python(service: str, code: str) -> str:
            return dc("exec", "-T", service, "python", "-", code=code).stdout.strip()

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
            )
            print("PASS full-stack startup", flush=True)
            python(
                "api",
                """
import os, urllib.request, psycopg
assert "VESPERS_MIGRATION_DATABASE_URL" not in os.environ
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
            query_prefix = """
import os, psycopg
c=psycopg.connect(os.environ["VESPERS_DBOS_SYSTEM_DATABASE_URL"].replace("postgresql+psycopg:","postgresql:"))
"""
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
secret=b'synthetic-adapter-key-do-not-persist'
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
            assert "synthetic-adapter-key-do-not-persist" not in logs
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
        finally:
            dc("down", "-v", "--remove-orphans")
            print("Removed disposable project " + project, flush=True)


if __name__ == "__main__":
    main()
