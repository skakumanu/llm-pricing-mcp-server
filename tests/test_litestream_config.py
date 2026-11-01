"""Litestream replication, completeness guard and restore docs (v1.86.0, AC19-AC21)."""
import os
import re
import stat
import subprocess
import sys
from pathlib import Path


project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from scripts import check_replication, launch_checklist  # noqa: E402

DOCKERFILE = (project_root / "Dockerfile").read_text(encoding="utf-8")
ENTRYPOINT = project_root / "entrypoint.sh"
LITESTREAM_YML = (project_root / "litestream.yml").read_text(encoding="utf-8")
DEPLOYMENT = (project_root / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
DBS = ("billing.db", "pricing_history.db", "orchestrator.db")


class TestImageAndConfig:
    def test_dockerfile_installs_litestream_and_uses_entrypoint(self):
        assert re.search(r"^FROM litestream/litestream:\d+\.\d+\.\d+ AS litestream$", DOCKERFILE, re.M)
        assert "COPY --from=litestream /usr/local/bin/litestream /usr/local/bin/litestream" in DOCKERFILE
        assert "COPY litestream.yml /etc/litestream.yml" in DOCKERFILE
        assert "COPY entrypoint.sh" in DOCKERFILE
        assert re.search(r'^CMD \["/app/entrypoint\.sh"\]$', DOCKERFILE, re.M)
        assert "USER appuser" in DOCKERFILE

    def test_entrypoint_is_executable_shell(self):
        assert ENTRYPOINT.read_text(encoding="utf-8").startswith("#!/bin/sh")
        assert os.stat(ENTRYPOINT).st_mode & stat.S_IXUSR
        assert subprocess.run(["sh", "-n", str(ENTRYPOINT)]).returncode == 0

    def test_litestream_yml_names_the_three_databases(self):
        paths = check_replication.litestream_paths()
        assert paths == [f"/app/data/{d}" for d in DBS]
        for db in DBS:
            assert f"url: ${{LITESTREAM_REPLICA_URL}}/{db}" in LITESTREAM_YML

    def test_part_b_database_is_not_listed_yet(self):
        assert "conversations.db" not in LITESTREAM_YML

    def test_no_credential_looking_values_in_repo_files(self):
        pattern = re.compile(
            r"(LITESTREAM_(ACCESS_KEY_ID|SECRET_ACCESS_KEY)\s*[:=]\s*['\"]?[A-Za-z0-9/+]{8,}|AKIA[A-Z0-9]{16})")
        files = [project_root / n for n in ("litestream.yml", "entrypoint.sh", "Dockerfile", "fly.toml", ".env.example")]
        files += [project_root / "docs" / "DEPLOYMENT.md", project_root / "README.md"]
        for f in files:
            assert not pattern.search(f.read_text(encoding="utf-8")), f

    def test_docs_shell_examples_use_var_references(self):
        block = DEPLOYMENT[DEPLOYMENT.index("flyctl secrets set LITESTREAM_REPLICA_URL"):]
        block = block[:block.index("```")]
        for name in ("LITESTREAM_REPLICA_URL", "LITESTREAM_ACCESS_KEY_ID", "LITESTREAM_SECRET_ACCESS_KEY"):
            assert f'{name}="${name}"' in block

    def test_litestream_variables_are_not_settings_fields(self):
        from src.config.settings import Settings
        assert not [f for f in Settings.model_fields if "litestream" in f.lower()]
        env_example = (project_root / ".env.example").read_text(encoding="utf-8")
        for line in env_example.splitlines():
            if "LITESTREAM" in line:
                assert line.lstrip().startswith("#"), line

    def test_pyyaml_is_pinned(self):
        assert re.search(r"^pyyaml==\d", (project_root / "requirements.txt").read_text(encoding="utf-8"), re.I | re.M)


class TestCompletenessGuard:
    def test_real_inputs_pass(self):
        found = check_replication.check(
            check_replication.db_path_fields(), check_replication.fly_env(), check_replication.litestream_paths())
        assert found == []
        assert check_replication.main([]) == 0

    def test_enumerates_the_three_production_databases(self):
        assert check_replication.db_path_fields() == [
            "billing_db_path", "orchestrator_db_path", "pricing_history_db_path"]

    def test_injected_fake_setting_fails(self):
        fields = check_replication.db_path_fields() + ["fake_db_path"]
        found = check_replication.check(fields, check_replication.fly_env(), check_replication.litestream_paths())
        assert found and any("fake_db_path" in p for p in found)

    def test_unlisted_volume_database_fails(self):
        env = dict(check_replication.fly_env(), FAKE_DB_PATH="/app/data/fake.db")
        found = check_replication.check(
            check_replication.db_path_fields() + ["fake_db_path"], env, check_replication.litestream_paths())
        assert any("not listed in litestream.yml" in p for p in found)

    def test_database_off_the_volume_fails(self):
        env = dict(check_replication.fly_env(), BILLING_DB_PATH="/tmp/billing.db")
        found = check_replication.check(check_replication.db_path_fields(), env, check_replication.litestream_paths())
        assert any("not under /app/data" in p for p in found)

    def test_optional_store_set_in_fly_must_be_listed(self):
        env = dict(check_replication.fly_env(), CONVERSATION_DB_PATH="/app/data/conversations.db")
        found = check_replication.check(check_replication.db_path_fields(), env, check_replication.litestream_paths())
        assert any("conversation_db_path" in p for p in found)


class TestEntrypoint:
    def _run(self, tmp_path, env_extra):
        bindir = tmp_path / "bin"
        bindir.mkdir()
        for name in ("python", "litestream"):
            stub = bindir / name
            stub.write_text('#!/bin/sh\necho "$(basename "$0") $*"\n', encoding="utf-8")
            stub.chmod(0o755)
        env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
        env.update(env_extra)
        # /etc/litestream.yml is only read when replication is on; give the script a copy to read
        script = ENTRYPOINT.read_text(encoding="utf-8").replace("/etc/litestream.yml", str(project_root / "litestream.yml"))
        path = tmp_path / "entrypoint.sh"
        path.write_text(script, encoding="utf-8")
        return subprocess.run(["sh", str(path)], env=env, capture_output=True, text=True, timeout=30)

    def test_falls_back_to_plain_uvicorn_without_litestream_variables(self, tmp_path):
        out = self._run(tmp_path, {})
        assert out.returncode == 0
        assert "python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 2" in out.stdout
        assert "litestream" not in out.stdout

    def test_replicates_and_restores_first_when_url_is_set(self, tmp_path):
        out = self._run(tmp_path, {"LITESTREAM_REPLICA_URL": "s3://bucket/prefix"})
        assert out.returncode == 0
        lines = out.stdout.splitlines()
        restores = [ln for ln in lines if ln.startswith("litestream restore")]
        assert len(restores) == 3
        for db, line in zip(DBS, restores):
            assert "-if-db-not-exists" in line and "-if-replica-exists" in line and line.endswith(f"/app/data/{db}")
        replicate = [ln for ln in lines if ln.startswith("litestream replicate")]
        assert len(replicate) == 1 and "-exec" in replicate[0] and "uvicorn" in replicate[0]
        assert lines.index(replicate[0]) > lines.index(restores[-1])


def test_health_check_passes_without_litestream_variables(monkeypatch):
    for key in list(os.environ):
        if key.startswith("LITESTREAM_"):
            monkeypatch.delenv(key)
    from fastapi.testclient import TestClient
    from src.main import app
    assert TestClient(app).get("/health").status_code == 200


class TestRestoreDocs:
    def _section(self):
        start = DEPLOYMENT.index("## Restore from the Litestream replica")
        rest = DEPLOYMENT[start + 5:]
        end = rest.find("\n## ")
        return rest[:end] if end != -1 else rest

    def test_titled_section_with_ordered_steps_from_an_empty_volume(self):
        section = self._section()
        steps = re.findall(r"^(\d+)\. (.*)$", section, re.M)
        assert [int(n) for n, _ in steps] == list(range(1, len(steps) + 1)) and len(steps) >= 5
        text = " ".join(t for _, t in steps).lower()
        assert "empty volume" in text
        assert text.index("volumes create") < text.index("machine start")

    def test_each_database_is_restored(self):
        section = self._section()
        for db in DBS:
            assert f'litestream restore -o /app/data/{db} "$LITESTREAM_REPLICA_URL/{db}"' in section

    def test_ends_with_a_verification_step(self):
        steps = re.findall(r"^(\d+)\. (.*)$", self._section(), re.M)
        last = steps[-1][1].lower()
        assert last.startswith("verify") and "/health" in last

    def test_checklist_restore_drill_references_the_section(self):
        row = next(r for r in launch_checklist.parse() if r["ID"] == "LC-10")
        assert "DEPLOYMENT.md#restore-from-the-litestream-replica" in row["Verification"]
        anchor = "restore-from-the-litestream-replica"
        assert re.sub(r"[^a-z0-9]+", "-", "Restore from the Litestream replica".lower()).strip("-") == anchor
