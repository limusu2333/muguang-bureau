from __future__ import annotations

import asyncio
import unittest
import tempfile
import os
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from 多用户.部署 import 候选验收, 平台命令, 监督服务命令, 本机入口


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "多用户" / "部署"


class 部署边界测试(unittest.TestCase):
    def test_本机入口会把断开传给另一端而不残留转发任务(self):
        class Reader:
            def __init__(self):
                self.chunks = [b"hello", b""]

            async def read(self, _size):
                return self.chunks.pop(0)

        class Writer:
            def __init__(self):
                self.data = bytearray()
                self.eof = False

            def write(self, data):
                self.data.extend(data)

            async def drain(self):
                return None

            def write_eof(self):
                self.eof = True

        writer = Writer()
        asyncio.run(本机入口._copy(Reader(), writer))
        self.assertEqual(bytes(writer.data), b"hello")
        self.assertTrue(writer.eof)

    def test_本机模型依赖不会装进正式用户镜像(self):
        base = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        host = (ROOT / "requirements-host.txt").read_text(encoding="utf-8")
        dockerfile = (DEPLOY / "Dockerfile.公司").read_text(encoding="utf-8")

        self.assertNotIn("mlx==", base)
        self.assertNotIn("mlx-lm==", base)
        self.assertIn("-r requirements.txt", host)
        self.assertIn("-r 多用户/requirements.txt", host)
        self.assertIn("mlx==0.32.1", host)
        self.assertIn("mlx-lm==0.31.3", host)
        self.assertIn("COPY requirements.txt", dockerfile)
        self.assertNotIn("requirements-host.txt", dockerfile)

    def test_宿主身份同时绑定三份依赖清单(self):
        host_python = Path("/tmp/host-python").resolve()
        identity = {
            "schema": 1,
            "python_executable": str(host_python),
            "python_version": "3.14.0",
            "pip_freeze_sha256": "a" * 64,
        }
        result = SimpleNamespace(returncode=0, stdout=__import__("json").dumps(identity))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "requirements.txt").write_text("httpx==1\n", encoding="utf-8")
            (root / "requirements-host.txt").write_text(
                "-r requirements.txt\n-r 多用户/requirements.txt\nmlx==1\n", encoding="utf-8",
            )
            (root / "多用户-requirements.txt").write_text("pwdlib==1\n", encoding="utf-8")
            with (
                patch.object(监督服务命令, "BASE_REQUIREMENTS", root / "requirements.txt"),
                patch.object(监督服务命令, "HOST_REQUIREMENTS", root / "requirements-host.txt"),
                patch.object(
                    监督服务命令,
                    "PLATFORM_REQUIREMENTS",
                    root / "多用户-requirements.txt",
                ),
                patch.object(监督服务命令.subprocess, "run", return_value=result),
            ):
                first = 监督服务命令._运行环境身份(host_python)
                (root / "requirements.txt").write_text("httpx==2\n", encoding="utf-8")
                second = 监督服务命令._运行环境身份(host_python)
                (root / "多用户-requirements.txt").write_text("pwdlib==2\n", encoding="utf-8")
                third = 监督服务命令._运行环境身份(host_python)

        self.assertNotEqual(first["requirements_sha256"], second["requirements_sha256"])
        self.assertNotEqual(
            first["base_requirements_sha256"], second["base_requirements_sha256"],
        )
        self.assertEqual(
            first["host_requirements_sha256"], second["host_requirements_sha256"],
        )
        self.assertEqual(
            first["platform_requirements_sha256"], second["platform_requirements_sha256"],
        )
        self.assertNotEqual(second["requirements_sha256"], third["requirements_sha256"])
        self.assertNotEqual(
            second["platform_requirements_sha256"], third["platform_requirements_sha256"],
        )

    @staticmethod
    def _发布镜像清单(root: Path) -> dict:
        components = {}
        for name, marker in (("company", "a"), ("runner", "b"), ("platform", "c")):
            components[name] = {
                "id": "sha256:" + marker * 64,
                "candidate_tag": f"xj-{name}:candidate-fixed",
            }
        archive = root / "images.tar"
        archive.write_bytes(b"image archive")
        return {
            "version": "twilight-v1.0-beta",
            "path": str(root),
            "images": {"archive": "images.tar", "components": components},
        }

    @staticmethod
    def _加入外部镜像锁(release: dict) -> dict:
        external = {}
        for (name, source_ref), marker in zip(
            平台命令._外部运行镜像.items(), ("d", "e", "f"), strict=True,
        ):
            external[name] = {
                "source_ref": source_ref,
                "id": "sha256:" + marker * 64,
                "repo_digests": [source_ref.split(":", 1)[0] + "@sha256:" + marker * 64],
                "os": "linux",
                "architecture": "arm64",
            }
        release["images"]["external"] = external
        return release

    def test_镜像归档恢复后逐个核对原始镜像编号(self):
        with tempfile.TemporaryDirectory() as td:
            release = self._发布镜像清单(Path(td))
            loaded = False
            tags: dict[str, str] = {}
            commands: list[list[str]] = []

            def image_info(image: str):
                if image in tags:
                    return {"Id": tags[image]}
                ids = {
                    item["id"] for item in release["images"]["components"].values()
                }
                if loaded and image in ids:
                    return {"Id": image}
                return None

            def run(command, **kwargs):
                nonlocal loaded
                commands.append(command)
                if command[1:3] == ["image", "load"]:
                    loaded = True
                elif command[1:3] == ["image", "tag"]:
                    tags[command[-1]] = command[-2]

            with (
                patch.object(平台命令, "_镜像信息", side_effect=image_info),
                patch.object(平台命令, "_run", side_effect=run),
            ):
                平台命令._恢复清单镜像(release)

            self.assertEqual(commands[0][1:3], ["image", "load"])
            self.assertEqual(len([c for c in commands if c[1:3] == ["image", "tag"]]), 3)

    def test_正式标签若指向其他内容会拒绝覆盖(self):
        with tempfile.TemporaryDirectory() as td:
            release = self._发布镜像清单(Path(td))
            commands: list[list[str]] = []

            def image_info(image: str):
                if image == "xj-company:twilight-v1.0-beta":
                    return {"Id": "sha256:" + "f" * 64}
                return None

            with (
                patch.object(平台命令, "_恢复清单镜像"),
                patch.object(平台命令, "_镜像信息", side_effect=image_info),
                patch.object(平台命令, "_run", side_effect=lambda command, **kwargs: commands.append(command)),
            ):
                with self.assertRaisesRegex(RuntimeError, "拒绝覆盖"):
                    平台命令.提升版本镜像(release)

            self.assertEqual(commands, [])

    def test_未激活失败只清理自己留下的正式标签(self):
        release = self._发布镜像清单(Path(tempfile.mkdtemp()))
        removed: set[str] = set()
        commands: list[list[str]] = []
        components = release["images"]["components"]

        # The real tags are checked against their matching component IDs before removal.
        tag_ids = {
            "xj-company:twilight-v1.0-beta": components["company"]["id"],
            "xj-runner:twilight-v1.0-beta": components["runner"]["id"],
            "xj-platform:twilight-v1.0-beta": components["platform"]["id"],
        }

        def inspect(image: str):
            return None if image in removed else {"Id": tag_ids.get(image, "")}

        def run(command, **kwargs):
            commands.append(command)
            if command[1:3] == ["image", "rm"]:
                removed.add(command[-1])

        with (
            patch.object(平台命令, "_镜像信息", side_effect=inspect),
            patch.object(平台命令, "_run", side_effect=run),
        ):
            平台命令.撤销未激活版本镜像(release)

        self.assertEqual(
            [command[-1] for command in commands],
            [
                "xj-company:twilight-v1.0-beta",
                "xj-runner:twilight-v1.0-beta",
                "xj-platform:twilight-v1.0-beta",
            ],
        )

    def test_开发镜像必须匹配当前代码指纹(self):
        current = "a" * 64

        def image_info(image: str):
            source = current if image != "xj-platform:dev" else "outdated"
            return {
                "Id": f"sha256:{image}",
                "Config": {"Labels": {
                    "org.xj.company.version": "dev",
                    "org.xj.company.formal-source-sha256": source,
                }},
            }

        with (
            patch.object(平台命令, "当前已发布版本", return_value=None),
            patch.object(平台命令, "_开发源指纹", return_value=current),
            patch.object(平台命令, "_镜像信息", side_effect=image_info),
        ):
            self.assertFalse(平台命令._镜像齐全())

        with (
            patch.object(平台命令, "当前已发布版本", return_value=None),
            patch.object(平台命令, "_开发源指纹", return_value=current),
            patch.object(
                平台命令, "_镜像信息",
                side_effect=lambda image: {
                    "Id": f"sha256:{image}",
                    "Config": {"Labels": {
                        "org.xj.company.version": "dev",
                        "org.xj.company.formal-source-sha256": current,
                    }},
                },
            ),
        ):
            self.assertTrue(平台命令._镜像齐全())

    def test_开发镜像先完整构建候选再替换旧标签(self):
        commands: list[list[str]] = []
        cleanup: list[list[str]] = []

        def run(command, *, env=None):
            commands.append(command)

        with (
            patch.object(平台命令, "当前已发布版本", return_value=None),
            patch.object(平台命令, "_开发源指纹", return_value="b" * 64),
            patch.object(平台命令, "_镜像存在", return_value=True),
            patch.object(
                平台命令, "_镜像标签匹配",
                side_effect=lambda image, **kwargs: "-candidate-fixed" in image,
            ),
            patch.object(平台命令.secrets, "token_hex", return_value="fixed"),
            patch.object(平台命令, "_run", side_effect=run),
            patch.object(
                平台命令.subprocess, "run",
                side_effect=lambda command, **kwargs: cleanup.append(command) or SimpleNamespace(returncode=0),
            ),
        ):
            self.assertTrue(平台命令.构建镜像())

        build_commands = [command for command in commands if command[1:2] == ["build"]]
        tag_commands = [command for command in commands if command[1:3] == ["image", "tag"]]
        self.assertEqual(len(build_commands), 3)
        self.assertEqual(len(tag_commands), 3)
        self.assertTrue(all("-candidate-fixed" in command[-2] for command in build_commands))
        self.assertEqual(
            [command[-1] for command in tag_commands],
            ["xj-company:dev", "xj-runner:dev", "xj-platform:dev"],
        )
        self.assertTrue(all("-candidate-fixed" in image for image in cleanup[0][3:]))
        self.assertNotIn("xj-platform:dev", cleanup[0][3:])

    def test_运行容器仍指向旧镜像时判定需更新(self):
        def inspect(command, **kwargs):
            container = command[-1]
            image_id = "sha256:old" if container == "xj-gw" else "sha256:current"
            return SimpleNamespace(returncode=0, stdout=image_id + "\n")

        with (
            patch.object(平台命令, "_镜像齐全", return_value=True),
            patch.object(平台命令, "当前已发布版本", return_value=None),
            patch.object(平台命令, "_镜像信息", return_value={"Id": "sha256:current"}),
            patch.object(平台命令.subprocess, "run", side_effect=inspect),
        ):
            self.assertFalse(平台命令.运行镜像当前())

    def test_运行检查按已发布Compose清单不要求开发版服务(self):
        with tempfile.TemporaryDirectory() as td:
            compose = Path(td) / "compose.yaml"
            compose.write_text(
                """
services:
  search-gw:
    image: ${XJ_PLATFORM_IMAGE:-xj-platform:dev}
    container_name: xj-search-gw
  gw:
    image: ${XJ_PLATFORM_IMAGE:-xj-platform:dev}
    container_name: xj-gw
""",
                encoding="utf-8",
            )
            inspected: list[str] = []

            def inspect(command, **kwargs):
                inspected.append(command[-1])
                return SimpleNamespace(returncode=0, stdout="sha256:current\n")

            with (
                patch.object(平台命令, "_镜像齐全", return_value=True),
                patch.object(平台命令, "当前已发布版本", return_value="twilight-v1.0-beta"),
                patch.object(平台命令, "读取正式版本", return_value={}),
                patch.object(平台命令, "_发布Compose", return_value=compose),
                patch.object(平台命令, "_镜像信息", return_value={"Id": "sha256:current"}),
                patch.object(平台命令.subprocess, "run", side_effect=inspect),
            ):
                self.assertTrue(平台命令.运行镜像当前())

            self.assertEqual(inspected, ["xj-search-gw", "xj-gw"])

    def test_旧发布Compose忽略开发版才有的服务(self):
        with tempfile.TemporaryDirectory() as td:
            compose = Path(td) / "compose.yaml"
            compose.write_text(
                """
services:
  search-gw:
    image: ${XJ_PLATFORM_IMAGE:-xj-platform:dev}
    container_name: xj-search-gw
""",
                encoding="utf-8",
            )
            commands: list[list[str]] = []

            def run(command, **kwargs):
                commands.append(command)

            with (
                patch.object(平台命令, "_compose_env", return_value={}),
                patch.object(平台命令, "_run", side_effect=run),
            ):
                平台命令._compose_up(
                    "local-rerank-bridge", "search-gw",
                    provider="local", compose_file=compose,
                )

            self.assertEqual(len(commands), 1)
            self.assertIn("search-gw", commands[0])
            self.assertNotIn("local-rerank-bridge", commands[0])

    def test_活动锁暂指向候选时仍按实际旧容器识别平台(self):
        """正式发布先更新活动锁，旧平台识别不能因此要求候选服务。"""
        inspected: list[str] = []

        def inspect(command, **kwargs):
            container = command[-1]
            inspected.append(container)
            if container == "xj-local-rerank-bridge":
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout="xj-platform:twilight-v1.0-beta\n",
                stderr="",
            )

        old_compose = ROOT / "old-formal-compose.yaml"
        old_containers = (
            "xj-search-gw", "xj-supervisor-bridge", "xj-gw", "xj-local-entry",
        )
        with (
            patch.object(平台命令, "_镜像对应发布", return_value={"version": "twilight-v1.0-beta"}),
            patch.object(平台命令, "_发布Compose", return_value=old_compose) as publish_compose,
            patch.object(
                平台命令,
                "_发布平台服务与容器",
                return_value=(
                    ("search-gw", "supervisor-bridge", "gw", "local-entry"),
                    old_containers,
                ),
            ),
            patch.object(平台命令.subprocess, "run", side_effect=inspect),
        ):
            result = 平台命令.当前平台镜像()

        self.assertEqual(result, "xj-platform:twilight-v1.0-beta")
        self.assertEqual(publish_compose.call_args.kwargs["image"], "xj-platform:twilight-v1.0-beta")
        self.assertNotIn("xj-local-rerank-bridge", inspected[4:])

    def test_旧发布不强行迁移新宿主而新发布会启用(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = root / "old"
            new = root / "new"
            (old / "多用户" / "部署").mkdir(parents=True)
            (new / "多用户" / "部署").mkdir(parents=True)
            (new / "多用户" / "部署" / "宿主启动器.py").write_text("", encoding="utf-8")
            self.assertFalse(平台命令._发布具备三角色宿主({"build_context": str(old)}))
            self.assertTrue(平台命令._发布具备三角色宿主({"build_context": str(new)}))

    def test_平台切换把Compose服务名与容器名分开(self):
        compose_calls: list[list[str]] = []
        observed = iter(("xj-platform:dev", "xj-platform:twilight-v1.0-beta"))
        cleanup_calls: list[Path] = []
        events: list[str] = []

        def compose_up(*services, **_kwargs):
            compose_calls.append(list(services))
            events.append("compose")

        def cleanup(compose):
            cleanup_calls.append(compose)
            events.append("cleanup")

        with (
            patch.object(平台命令, "当前平台镜像", side_effect=lambda: next(observed)),
            patch.object(平台命令, "_镜像信息", return_value={"Id": "sha256:platform"}),
            patch.object(平台命令, "读取平台", return_value="local"),
            patch.object(平台命令, "_镜像对应发布", return_value=None),
            patch.object(平台命令, "_发布Compose", return_value=ROOT / "多用户" / "部署" / "compose.yaml"),
            patch.object(平台命令, "_核对平台来源"),
            patch.object(
                平台命令,
                "_清理目标未声明平台容器",
                side_effect=cleanup,
            ),
            patch.object(平台命令, "_compose_up", side_effect=compose_up),
            patch.object(平台命令, "_恢复用户网络", side_effect=lambda: events.append("network")),
        ):
            result = 平台命令.切换平台版本("xj-platform:twilight-v1.0-beta")

        self.assertTrue(result["changed"])
        self.assertEqual(
            compose_calls,
            [
                ["postgres"],
                ["model-proxy", "local-rerank-bridge", "search-gw", "supervisor-bridge", "gw", "local-entry"],
            ],
        )
        self.assertEqual(len(cleanup_calls), 1)
        self.assertEqual(cleanup_calls[0].name, "compose.yaml")
        self.assertEqual(cleanup_calls[0].parent.name, "部署")
        self.assertEqual(events, ["compose", "compose", "network", "cleanup"])

    def test_平台切换成功只清理目标未声明的白名单容器(self):
        with tempfile.TemporaryDirectory() as td:
            compose = Path(td) / "compose.yaml"
            compose.write_text(
                """
services:
  search-gw:
    image: ${XJ_PLATFORM_IMAGE:-xj-platform:dev}
    container_name: xj-search-gw
  gw:
    image: ${XJ_PLATFORM_IMAGE:-xj-platform:dev}
    container_name: xj-gw
""",
                encoding="utf-8",
            )
            docker_calls: list[list[str]] = []

            def docker_run(command, **kwargs):
                docker_calls.append(command)
                if command[1:3] == ["container", "ls"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout="xj-local-rerank-bridge\nxj-postgres\nxj-model-proxy\n",
                        stderr="",
                    )
                raise AssertionError(f"不应执行其他 Docker 查询：{command}")

            with (
                patch.object(平台命令.subprocess, "run", side_effect=docker_run),
                patch.object(平台命令, "_run", side_effect=lambda command, **kwargs: docker_calls.append(command)),
            ):
                removed = 平台命令._清理目标未声明平台容器(compose)

            self.assertEqual(removed, ("xj-local-rerank-bridge",))
            expected_docker = os.environ.get("XJ_DOCKER_BIN", "docker").strip() or "docker"
            self.assertEqual(
                docker_calls[-1],
                [expected_docker, "container", "rm", "--force", "xj-local-rerank-bridge"],
            )
            self.assertNotIn("xj-postgres", " ".join(" ".join(item) for item in docker_calls))
            self.assertNotIn("xj-model-proxy", " ".join(" ".join(item) for item in docker_calls if item[1:3] == ["container", "rm"]))

    def test_平台切换失败自动回退时两套目标边界都会清理(self):
        compose_target = ROOT / "target-compose.yaml"
        compose_previous = ROOT / "previous-compose.yaml"
        observed = iter(("xj-platform:twilight-old", "xj-platform:twilight-new"))
        cleanup_calls: list[Path] = []
        verify_calls = iter((RuntimeError("目标健康核对失败"), None))

        def verify(*_args, **_kwargs):
            result = next(verify_calls)
            if isinstance(result, BaseException):
                raise result
            return result

        with (
            patch.object(平台命令, "当前平台镜像", side_effect=lambda: next(observed)),
            patch.object(平台命令, "_镜像信息", return_value={"Id": "sha256:platform"}),
            patch.object(平台命令, "_读", return_value="local"),
            patch.object(平台命令, "_镜像对应发布", side_effect=(None, {"version": "old"})),
            patch.object(平台命令, "_发布Compose", side_effect=(compose_target, compose_previous)),
            patch.object(平台命令, "_核对平台来源", side_effect=verify),
            patch.object(平台命令, "_compose_up"),
            patch.object(平台命令, "_恢复用户网络") as restore_networks,
            patch.object(
                平台命令,
                "_清理目标未声明平台容器",
                side_effect=lambda compose: cleanup_calls.append(compose),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "目标健康核对失败"):
                平台命令.切换平台版本(
                    "xj-platform:twilight-new", release={"version": "twilight-new"},
                )

        self.assertEqual(cleanup_calls, [compose_target, compose_previous])
        self.assertEqual(restore_networks.call_count, 2)

    def test_管理员指定回退同样按目标Compose清理(self):
        target = "xj-platform:twilight-v1.0-beta"
        compose = ROOT / "archived-compose.yaml"
        cleanup_calls: list[Path] = []

        with (
            patch.object(平台命令, "当前平台镜像", return_value="xj-platform:new"),
            patch.object(
                平台命令,
                "_读取正式回退发布",
                return_value={"version": "twilight-v1.0-beta"},
            ),
            patch.object(平台命令, "_发布Compose", return_value=compose),
            patch.object(平台命令, "_镜像信息", return_value={"Id": "sha256:platform"}),
            patch.object(平台命令, "_读", return_value="local"),
            patch.object(平台命令, "_compose_up"),
            patch.object(平台命令, "_恢复用户网络") as restore_networks,
            patch.object(平台命令, "_核对平台来源"),
            patch.object(
                平台命令,
                "_清理目标未声明平台容器",
                side_effect=lambda compose_file: cleanup_calls.append(compose_file),
            ),
        ):
            result = 平台命令.恢复平台版本(target)

        self.assertTrue(result["changed"])
        self.assertEqual(cleanup_calls, [compose])
        restore_networks.assert_called_once_with()

    def test_管理员指定回退可收口新旧服务混合状态(self):
        target = "xj-platform:twilight-v1.0-beta"
        compose = ROOT / "archived-compose.yaml"
        switched: list[tuple[str, Path]] = []

        with (
            patch.object(
                平台命令, "当前平台镜像",
                side_effect=RuntimeError("共享平台容器不是同一版本"),
            ),
            patch.object(
                平台命令, "_读取正式回退发布",
                return_value={"version": "twilight-v1.0-beta"},
            ),
            patch.object(平台命令, "_发布Compose", return_value=compose),
            patch.object(平台命令, "_镜像信息", return_value={"Id": "sha256:platform"}),
            patch.object(平台命令, "_读", return_value="local"),
            patch.object(
                平台命令, "_切换Compose堆栈",
                side_effect=lambda **kwargs: switched.append(
                    (str(kwargs["platform_image"]), Path(kwargs["compose_file"]))
                ),
            ),
            patch.object(平台命令, "_核对平台来源"),
        ):
            result = 平台命令.恢复平台版本(target)

        self.assertEqual(result["previous_image"], "")
        self.assertTrue(result["changed"])
        self.assertEqual(switched, [(target, compose)])

    def test_发布事务回退可收口新旧服务混合状态(self):
        target = "xj-platform:twilight-v1.0-beta"
        compose = ROOT / "archived-compose.yaml"
        switched: list[str] = []

        with (
            patch.object(
                平台命令, "当前平台镜像",
                side_effect=RuntimeError("共享平台容器不是同一版本"),
            ),
            patch.object(
                平台命令, "_读取正式回退发布",
                return_value={"version": "twilight-v1.0-beta"},
            ),
            patch.object(平台命令, "_发布Compose", return_value=compose),
            patch.object(平台命令, "_镜像信息", return_value={"Id": "sha256:platform"}),
            patch.object(平台命令, "_读", return_value="local"),
            patch.object(
                平台命令, "_切换Compose堆栈",
                side_effect=lambda **kwargs: switched.append(str(kwargs["platform_image"])),
            ),
            patch.object(平台命令, "_核对平台来源"),
        ):
            result = 平台命令.恢复事务平台版本(target)

        self.assertEqual(result["previous_image"], "")
        self.assertTrue(result["changed"])
        self.assertEqual(switched, [target])

    def test_管理员回退拒绝开发镜像且不读取当前运行面(self):
        current = Mock()
        with patch.object(平台命令, "当前平台镜像", current):
            with self.assertRaisesRegex(RuntimeError, "不能恢复到开发版"):
                平台命令.恢复平台版本("xj-platform:dev")
        current.assert_not_called()

    def test_管理员回退拒绝暂存目录(self):
        with tempfile.TemporaryDirectory() as td:
            staged = Path(td) / "staged" / "twilight-staged"
            staged.mkdir(parents=True)
            (staged / "manifest.json").write_text(
                '{"version":"twilight-staged","published_at":null}\n',
                encoding="utf-8",
            )
            with patch.object(
                平台命令,
                "_镜像对应发布",
                return_value={"path": str(staged)},
            ):
                with self.assertRaisesRegex(RuntimeError, "已发布目录"):
                    平台命令._读取正式回退发布("xj-platform:twilight-staged")

    def test_确保平台版本即使镜像相同也核对并修复模型配置来源(self):
        target = "xj-platform:twilight-v1.0-beta"
        compose = ROOT / "fake-release" / "compose.yaml"
        compose_calls: list[tuple[tuple[str, ...], dict]] = []

        with (
            patch.object(平台命令, "当前平台镜像", return_value=target),
            patch.object(平台命令, "_读取正式回退发布", return_value={"path": str(ROOT / "fake-release")}),
            patch.object(平台命令, "_发布Compose", return_value=compose),
            patch.object(
                平台命令,
                "_核对平台来源",
                side_effect=(RuntimeError("模型代理仍在使用旧配置"), None),
            ) as verify,
            patch.object(平台命令, "_清理目标未声明平台容器"),
            patch.object(平台命令, "_镜像信息", return_value={"Id": "sha256:platform"}),
            patch.object(平台命令, "_读", return_value="local"),
            patch.object(
                平台命令,
                "_compose_up",
                side_effect=lambda *services, **kwargs: compose_calls.append((services, kwargs)),
            ),
        ):
            changed = 平台命令.确保平台版本("twilight-v1.0-beta")

        self.assertTrue(changed)
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(len(compose_calls), 2)
        self.assertEqual(compose_calls[0][0], ("postgres",))
        services, kwargs = compose_calls[1]
        self.assertIn("model-proxy", services)
        self.assertTrue(kwargs["force_recreate"])
        self.assertTrue(kwargs["no_deps"])
        self.assertEqual(kwargs["platform_image"], target)
        self.assertEqual(kwargs["compose_file"], compose)

    def test_后台环境使用固定Docker路径(self):
        calls: list[list[str]] = []

        def run(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=1, stdout="")

        with (
            patch.dict(os.environ, {"XJ_DOCKER_BIN": "/usr/local/bin/docker"}),
            patch.object(平台命令.subprocess, "run", side_effect=run),
        ):
            self.assertIsNone(平台命令._镜像信息("xj-platform:twilight-v1.0-beta"))

        self.assertEqual(calls[0][0], "/usr/local/bin/docker")

    def test_Docker路径矩阵不改变镜像恢复清理和构建逻辑(self):
        docker_paths = (
            ("默认命令", None, "docker"),
            ("Intel或Docker链接", "/usr/local/bin/docker", "/usr/local/bin/docker"),
            ("Apple芯片Homebrew", "/opt/homebrew/bin/docker", "/opt/homebrew/bin/docker"),
            (
                "Docker应用内命令",
                "/Applications/Docker.app/Contents/Resources/bin/docker",
                "/Applications/Docker.app/Contents/Resources/bin/docker",
            ),
        )
        baseline: tuple[list[list[str]], ...] | None = None

        with tempfile.TemporaryDirectory() as td:
            release = self._发布镜像清单(Path(td))
            components = release["images"]["components"]

            for label, configured_path, expected_path in docker_paths:
                with self.subTest(docker=label):
                    environment = {} if configured_path is None else {"XJ_DOCKER_BIN": configured_path}
                    restore_commands: list[list[str]] = []
                    cleanup_commands: list[list[str]] = []
                    build_commands: list[list[str]] = []
                    build_cleanup_commands: list[list[str]] = []

                    loaded = False
                    restored_tags: dict[str, str] = {}

                    def restore_info(image: str):
                        if image in restored_tags:
                            return {"Id": restored_tags[image]}
                        image_ids = {item["id"] for item in components.values()}
                        if loaded and image in image_ids:
                            return {"Id": image}
                        return None

                    def restore_run(command, **kwargs):
                        nonlocal loaded
                        restore_commands.append(command)
                        if command[1:3] == ["image", "load"]:
                            loaded = True
                        elif command[1:3] == ["image", "tag"]:
                            restored_tags[command[-1]] = command[-2]

                    removed: set[str] = set()
                    final_tag_ids = {
                        f"xj-{name}:twilight-v1.0-beta": item["id"]
                        for name, item in components.items()
                    }

                    def cleanup_info(image: str):
                        return None if image in removed else {"Id": final_tag_ids.get(image, "")}

                    def cleanup_run(command, **kwargs):
                        cleanup_commands.append(command)
                        if command[1:3] == ["image", "rm"]:
                            removed.add(command[-1])

                    with patch.dict(os.environ, environment, clear=True):
                        with (
                            patch.object(平台命令, "_镜像信息", side_effect=restore_info),
                            patch.object(平台命令, "_run", side_effect=restore_run),
                        ):
                            平台命令._恢复清单镜像(release)

                        with (
                            patch.object(平台命令, "_镜像信息", side_effect=cleanup_info),
                            patch.object(平台命令, "_run", side_effect=cleanup_run),
                        ):
                            平台命令.撤销未激活版本镜像(release)

                        with (
                            patch.object(平台命令, "当前已发布版本", return_value=None),
                            patch.object(平台命令, "_开发源指纹", return_value="b" * 64),
                            patch.object(平台命令, "_镜像存在", return_value=True),
                            patch.object(
                                平台命令, "_镜像标签匹配",
                                side_effect=lambda image, **kwargs: "-candidate-fixed" in image,
                            ),
                            patch.object(平台命令.secrets, "token_hex", return_value="fixed"),
                            patch.object(
                                平台命令, "_run",
                                side_effect=lambda command, **kwargs: build_commands.append(command),
                            ),
                            patch.object(
                                平台命令.subprocess, "run",
                                side_effect=lambda command, **kwargs: (
                                    build_cleanup_commands.append(command)
                                    or SimpleNamespace(returncode=0)
                                ),
                            ),
                        ):
                            self.assertTrue(平台命令.构建镜像())

                    all_commands = (
                        restore_commands
                        + cleanup_commands
                        + build_commands
                        + build_cleanup_commands
                    )
                    self.assertTrue(all_commands)
                    self.assertTrue(all(command[0] == expected_path for command in all_commands))

                    normalized = (
                        [command[1:] for command in restore_commands],
                        [command[1:] for command in cleanup_commands],
                        [command[1:] for command in build_commands],
                        [command[1:] for command in build_cleanup_commands],
                    )
                    if baseline is None:
                        baseline = normalized
                    else:
                        self.assertEqual(normalized, baseline)

                    self.assertEqual(
                        len([command for command in build_commands if command[1:2] == ["build"]]),
                        3,
                    )
                    self.assertEqual(
                        len([command for command in build_commands if command[1:3] == ["image", "tag"]]),
                        3,
                    )

    def test_候选锁定三个外部镜像的精确编号(self):
        calls: list[list[str]] = []
        ids = {
            ref: "sha256:" + marker * 64
            for ref, marker in zip(平台命令._外部运行镜像.values(), ("d", "e", "f"), strict=True)
        }

        class Log:
            def 运行(self, command, **_kwargs):
                calls.append(command)
                return {"status": "passed"}

        def inspect(ref: str):
            image_id = ids[ref]
            return {
                "Id": image_id, "RepoDigests": [ref.split(":", 1)[0] + "@" + image_id],
                "Os": "linux", "Architecture": "arm64", "Size": 128 * 1024 * 1024,
            }

        with patch.object(平台命令, "_镜像信息", side_effect=inspect):
            locked = 平台命令._锁定外部运行镜像(
                docker="docker", env={"PATH": "/usr/bin"}, log=Log(),
            )

        self.assertEqual(set(locked), {"postgres", "model_proxy", "cloudflared"})
        self.assertEqual([call[1:3] for call in calls], [["image", "pull"]] * 3)
        for name, source_ref in 平台命令._外部运行镜像.items():
            self.assertEqual(locked[name]["source_ref"], source_ref)
            self.assertEqual(locked[name]["id"], ids[source_ref])

    def test_候选Compose只接收锁定的外部镜像编号(self):
        release = self._加入外部镜像锁(self._发布镜像清单(Path(tempfile.mkdtemp())))
        with (
            patch.object(平台命令, "检查平台配置"),
            patch.object(平台命令, "_读", side_effect=lambda name: "value-" + name),
            patch.object(平台命令, "当前已发布版本", return_value=None),
        ):
            env = 平台命令._compose_env(provider="local", release=release)

        for name, env_name in 平台命令._外部镜像环境变量.items():
            self.assertEqual(env[env_name], release["images"]["external"][name]["id"])

    def test_封存旧Compose只补旧版需要的云端精排变量(self):
        with tempfile.TemporaryDirectory() as td:
            old_compose = Path(td) / "compose.yaml"
            old_compose.write_text(
                """
services:
  search-gw:
    image: ${XJ_PLATFORM_IMAGE:-xj-platform:dev}
    environment:
      XJ_RERANK_USD_PER_MTOKENS: ${XJ_RERANK_USD_PER_MTOKENS:?required}
""",
                encoding="utf-8",
            )
            values = {"rerank-usd-per-mtokens": "0.074"}

            def read(name):
                return values.get(name, "value-" + name)

            with (
                patch.object(平台命令, "检查平台配置"),
                patch.object(平台命令, "_读", side_effect=read),
                patch.object(平台命令, "当前已发布版本", return_value=None),
            ):
                old_env = 平台命令._compose_env(
                    provider="tailscale", compose_file=old_compose,
                )
                dev_env = 平台命令._compose_env(
                    provider="tailscale", compose_file=DEPLOY / "compose.yaml",
                )

            self.assertEqual(old_env["XJ_RERANK_USD_PER_MTOKENS"], "0.074")
            self.assertNotIn("XJ_RERANK_USD_PER_MTOKENS", dev_env)

    def test_镜像归档就绪包含全部外部镜像身份(self):
        release = self._加入外部镜像锁(self._发布镜像清单(Path(tempfile.mkdtemp())))
        tag_ids = {
            item["candidate_tag"]: item["id"]
            for item in release["images"]["components"].values()
        }
        external_ids = {item["id"] for item in release["images"]["external"].values()}

        def inspect(ref: str):
            image_id = tag_ids.get(ref, ref if ref in external_ids else "")
            return {"Id": image_id} if image_id else None

        with patch.object(平台命令, "_镜像信息", side_effect=inspect):
            self.assertTrue(平台命令._清单镜像就绪(release, final_tags=False))

        missing = next(iter(external_ids))
        with patch.object(
            平台命令, "_镜像信息",
            side_effect=lambda ref: None if ref == missing else inspect(ref),
        ):
            self.assertFalse(平台命令._清单镜像就绪(release, final_tags=False))

    def test_部署后同时核对Compose引用和实际外部镜像编号(self):
        release = self._加入外部镜像锁(self._发布镜像清单(Path(tempfile.mkdtemp())))
        by_container = {
            平台命令._外部镜像容器[name]: item["id"]
            for name, item in release["images"]["external"].items()
        }

        def inspect(command, **_kwargs):
            image_id = by_container[command[-1]]
            return SimpleNamespace(returncode=0, stdout=image_id + "\n" + image_id + "\n")

        with (
            patch.object(平台命令.subprocess, "run", side_effect=inspect),
            patch.object(平台命令, "_等待Cloudflare隧道就绪"),
        ):
            平台命令._核对外部运行镜像(release, "cloudflare")

        def mutable_tag(command, **_kwargs):
            image_id = by_container[command[-1]]
            return SimpleNamespace(returncode=0, stdout="mutable:tag\n" + image_id + "\n")

        with patch.object(平台命令.subprocess, "run", side_effect=mutable_tag):
            with self.assertRaisesRegex(RuntimeError, "没有使用候选锁定身份"):
                平台命令._核对外部运行镜像(release, "cloudflare")

    def test_Cloudflare隧道未通过_ready时拒绝(self):
        class Response:
            status = 503

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with (
            patch.object(平台命令.urllib_request, "urlopen", return_value=Response()),
            patch.object(平台命令.time, "sleep"),
            patch.object(平台命令.time, "monotonic", side_effect=(0, 61)),
        ):
            with self.assertRaisesRegex(RuntimeError, "未就绪"):
                平台命令._等待Cloudflare隧道就绪(timeout=60)

    def test_候选运行时检查使用精确镜像且完全隔离(self):
        release = self._发布镜像清单(Path(tempfile.mkdtemp()))
        checks = 候选验收.隔离运行时检查步骤(
            "docker", release["images"]["components"],
        )
        self.assertEqual([item["kind"] for item in checks], [
            "service-entry", "one-shot-runtime", "service-entry",
        ])
        for item in checks:
            command = item["command"]
            self.assertIn("--rm", command)
            self.assertIn("--network", command)
            self.assertIn("none", command)
            self.assertIn("--read-only", command)
            self.assertNotIn("--mount", command)
            self.assertFalse(item["isolation"]["real_accounts"])
            self.assertTrue(item["boundary"])
            self.assertNotIn("compileall", " ".join(command))
        self.assertIn("/app/工具/机房.py", checks[0]["command"])
        self.assertIn("多用户.网关.启动", checks[2]["command"])

    def test_正式切换把同一候选锁传给数据库和共享服务(self):
        release = self._加入外部镜像锁(self._发布镜像清单(Path(tempfile.mkdtemp())))
        release["version"] = "twilight-v1.1-beta"
        calls: list[tuple[tuple[str, ...], dict]] = []
        observed = iter(("xj-platform:dev", "xj-platform:twilight-v1.1-beta"))
        with (
            patch.object(平台命令, "当前平台镜像", side_effect=lambda: next(observed)),
            patch.object(平台命令, "_发布Compose", return_value=ROOT / "compose.yaml"),
            patch.object(平台命令, "_镜像信息", return_value={"Id": "sha256:" + "c" * 64}),
            patch.object(平台命令, "_读", return_value="local"),
            patch.object(平台命令, "_核对平台来源"),
            patch.object(
                平台命令, "_compose_up",
                side_effect=lambda *services, **kwargs: calls.append((services, kwargs)),
            ),
            patch.object(平台命令, "_清理目标未声明平台容器"),
            patch.object(平台命令, "_恢复用户网络") as restore_networks,
        ):
            平台命令.切换平台版本("xj-platform:twilight-v1.1-beta", release=release)

        self.assertEqual([services for services, _kwargs in calls], [
            ("postgres",),
            ("model-proxy", "local-rerank-bridge", "search-gw", "supervisor-bridge", "gw", "local-entry"),
        ])
        self.assertTrue(all(kwargs["release"] is release for _services, kwargs in calls))
        restore_networks.assert_called_once_with()

    def test_Compose失败时指出具体异常服务而不是只显示命令(self):
        compose = DEPLOY / "compose.yaml"
        unhealthy = json.dumps([{
            "State": {"Status": "running", "ExitCode": 0, "Health": {"Status": "unhealthy"}},
        }])
        healthy = json.dumps([{
            "State": {"Status": "running", "ExitCode": 0, "Health": {"Status": "healthy"}},
        }])

        def inspect(command, **_kwargs):
            output = unhealthy if command[-1] == "xj-search-gw" else healthy
            return SimpleNamespace(returncode=0, stdout=output, stderr="")

        with (
            patch.object(平台命令, "_compose_env", return_value={}),
            patch.object(
                平台命令, "_run",
                side_effect=subprocess.CalledProcessError(1, ["docker", "compose", "up"]),
            ),
            patch.object(平台命令.subprocess, "run", side_effect=inspect),
        ):
            with self.assertRaisesRegex(RuntimeError, "search-gw 未通过健康检查"):
                平台命令._compose_up(
                    "local-rerank-bridge", "search-gw",
                    provider="local", compose_file=compose,
                )

    def test_共享服务网络权限固定(self):
        compose = yaml.safe_load((DEPLOY / "compose.yaml").read_text(encoding="utf-8"))
        services = compose["services"]
        self.assertEqual(services["gw"]["networks"], ["net_edge"])
        self.assertEqual(services["postgres"]["networks"], ["net_db"])
        self.assertEqual(set(services["model-proxy"]["networks"]), {"net_db", "egress"})
        self.assertEqual(set(services["search-gw"]["networks"]), {"net_db", "egress"})
        self.assertEqual(set(services["local-rerank-bridge"]["networks"]), {"net_db", "net_host"})
        self.assertEqual(set(services["supervisor-bridge"]["networks"]), {"net_edge", "net_host"})
        self.assertNotIn("ports", services["gw"])
        self.assertEqual(services["local-entry"]["ports"], ["127.0.0.1:37656:8080"])
        self.assertNotIn("environment", services["local-entry"])
        self.assertNotIn("volumes", services["gw"])
        self.assertEqual(services["gw"]["environment"]["XJ_SUPERVISOR_URL"], "http://supervisor-bridge:8765")
        self.assertEqual(services["model-proxy"]["ports"], ["127.0.0.1:4000:4000"])
        self.assertEqual(services["search-gw"]["ports"], ["127.0.0.1:4100:4100"])
        self.assertEqual(services["local-rerank-bridge"]["command"], ["python3", "-m", "多用户.部署.精排中继"])
        self.assertNotIn("XJ_RERANK_USD_PER_MTOKENS", services["search-gw"]["environment"])
        self.assertIn("XJ_LOCAL_RERANK_URL", services["search-gw"]["environment"])
        self.assertTrue(services["postgres"]["image"].startswith("${XJ_POSTGRES_IMAGE:-"))
        self.assertTrue(services["model-proxy"]["image"].startswith("${XJ_LITELLM_IMAGE:-"))
        self.assertTrue(services["cloudflared"]["image"].startswith("${XJ_CLOUDFLARED_IMAGE:-"))
        self.assertEqual(services["cloudflared"]["ports"], ["127.0.0.1:37658:2000"])
        self.assertEqual(
            services["cloudflared"]["command"],
            ["tunnel", "--no-autoupdate", "--loglevel", "info", "--metrics", "0.0.0.0:2000", "run", "--token", "${CF_TUNNEL_TOKEN:-}"],
        )

    def test_模型代理数据库不可用时拒绝请求(self):
        config = yaml.safe_load((DEPLOY / "litellm-config.yaml").read_text(encoding="utf-8"))
        self.assertIs(config["general_settings"]["allow_requests_on_db_unavailable"], False)
        self.assertNotIn("fail_closed_budget_enforcement", config["general_settings"])
        self.assertEqual(
            {item["model_name"] for item in config["model_list"]},
            {"gpt-5.6-sol", "qwen3.8-max", "glm-5.2", "deepseek-v4-pro-0813"},
        )

    def test_两个平台数据库互相不能连接(self):
        init = (DEPLOY / "postgres-init" / "01-create-databases.sh").read_text(encoding="utf-8")
        self.assertIn("REVOKE ALL PRIVILEGES ON DATABASE litellm FROM PUBLIC", init)
        self.assertIn("REVOKE ALL PRIVILEGES ON DATABASE xj_search FROM PUBLIC", init)

    def test_镜像只复制通用制度模板(self):
        dockerfile = (DEPLOY / "Dockerfile.公司").read_text(encoding="utf-8")
        self.assertIn("COPY 多用户/通用制度/ /opt/xj-policy-template/", dockerfile)
        self.assertIn("应用覆盖.mjs /build/前端", dockerfile)
        self.assertIn("COPY 多用户/控制面/ /app/多用户/控制面/", dockerfile)
        self.assertNotIn("COPY 多用户/ /app/多用户/", dockerfile)
        self.assertIn("多用户-requirements.txt", dockerfile)
        self.assertNotIn("COPY 班规.md", dockerfile)
        self.assertNotIn("COPY 花名册.yaml", dockerfile)
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertTrue(ignored.startswith("**\n"))
        self.assertNotIn("!本机实例.yaml", ignored)
        self.assertNotIn("!本机私密扫描词.txt", ignored)

    def test_用户镜像只使用不可变版本号(self):
        source = (DEPLOY / "平台命令.py").read_text(encoding="utf-8")
        self.assertIn('f"xj-company:{version}"', source)
        self.assertIn('f"xj-runner:{version}"', source)
        self.assertIn('f"xj-platform:{version}"', source)
        self.assertNotIn('"--tag", "xj-company:dev"', source)
        self.assertNotIn('"--tag", "xj-runner:dev"', source)

    def test_多用户前端称呼只在镜像副本动态化(self):
        overlay = (DEPLOY / "前端覆盖" / "应用覆盖.mjs").read_text(encoding="utf-8")
        identity = (DEPLOY / "前端覆盖" / "multiuserIdentity.ts").read_text(encoding="utf-8")
        self.assertIn('/instance.js', overlay)
        self.assertIn('instanceIdentity.callName', overlay)
        self.assertEqual(overlay.count('instanceIdentity.displayName'), 2)
        self.assertIn("import OwnerAccountMenu from './OwnerAccountMenu'", overlay)
        self.assertIn("import AccountMenu from './AccountMenu'", overlay)
        self.assertIn('<OwnerAccountMenu />', overlay)
        self.assertIn('<AccountMenu theme={theme} setTheme={setTheme} />', overlay)
        self.assertIn("import './styles/owner-account.css'", overlay)
        self.assertIn("import './styles/multiuserAccount.css'", overlay)
        self.assertIn("AnnouncementModal", overlay)
        self.assertIn("announcement.css", overlay)
        self.assertIn("RuntimeVersionBadge", overlay)
        self.assertIn("FeedbackModal", overlay)
        self.assertIn("feedback.css", overlay)
        self.assertIn('window.__XJ_INSTANCE__', identity)
        self.assertIn('accountId', identity)
        self.assertIn('bootId', identity)
        self.assertNotIn('COPY 多用户/部署/前端覆盖', (ROOT / "前端" / "package.json").read_text(encoding="utf-8"))

    def test_正式用户每次打开网页都会读取并显示有效公告(self):
        source = (DEPLOY / "前端覆盖" / "AnnouncementModal.tsx").read_text(encoding="utf-8")
        admin = (ROOT / "多用户" / "界面" / "static" / "admin.js").read_text(encoding="utf-8")
        admin_html = (ROOT / "多用户" / "界面" / "static" / "admin.html").read_text(encoding="utf-8")
        admin_css = (ROOT / "多用户" / "界面" / "static" / "admin.css").read_text(encoding="utf-8")
        announcement_css = (DEPLOY / "前端覆盖" / "announcement.css").read_text(encoding="utf-8")
        self.assertIn("fetch('/auth/announcements'", source)
        self.assertIn("requested.current", source)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("bootId", source)
        self.assertIn("body_rich", source)
        self.assertIn("dangerouslySetInnerHTML", source)
        self.assertIn("announcements.map", source)
        self.assertIn("最新公告在最上方", source)
        self.assertNotIn("setIndex", source)
        self.assertNotIn("announcement-pages", source)
        self.assertIn("下次打开公司网页时会显示", admin)
        self.assertIn("body_rich: announcementBody.innerHTML", admin)
        self.assertIn('data-announcement-action="edit"', admin)
        self.assertIn("method: editing ? 'PATCH' : 'POST'", admin)
        self.assertIn("data-editor-command=\"bold\"", admin_html)
        self.assertIn("data-editor-color=\"#b47a2f\"", admin_html)
        self.assertIn("data-editor-emoji=\"🎉\"", admin_html)
        self.assertIn("用户预览", admin_html)
        self.assertIn(".announcement-editor-layout", admin_css)
        self.assertIn(".announcement-color-gold", announcement_css)
        self.assertIn("正式用户每次打开公司网页时显示", admin_html)
        self.assertNotIn("用户下次重启公司时显示一次", admin_html)

    def test_平台镜像不整包复制构建材料(self):
        dockerfile = (DEPLOY / "Dockerfile.平台").read_text(encoding="utf-8")
        self.assertNotIn("COPY 多用户/ /app/多用户/", dockerfile)
        self.assertIn("多用户/部署/控制中继.py", dockerfile)

    def test_平台配置全部从Keychain解析(self):
        values = {name: "secret-value-" + name for name in 平台命令._内部凭据}
        for name in 平台命令._平台字段:
            values[name] = "secret-value-" + name
        values.update({
            "public-provider": "local",
            "openai-base": "https://models.example/v1",
            "qwen-base": "https://models.example/v1",
            "glm-base": "https://models.example/v1",
            "deepseek-base": "https://models.example/v1",
            "public-url": "https://company.example",
            "cf-access-issuer": "https://team.cloudflareaccess.com",
            "cf-account-id": "0" * 32,
            "embed-usd-per-mtokens": "0.1",
        })
        with patch.object(平台命令, "读取平台", side_effect=lambda name: values[name]):
            env = 平台命令._compose_env()
        for env_name, keychain_name in 平台命令._组合环境.items():
            self.assertEqual(env[env_name], values[keychain_name])
        self.assertEqual(env["XJ_LOCAL_TEST_MODE"], "1")
        self.assertEqual(env["CF_TUNNEL_TOKEN"], "")

    def test_Tailscale公网模式使用安全Cookie且不要求Cloudflare令牌(self):
        values = {name: "secret-value-" + name for name in 平台命令._内部凭据}
        for name in 平台命令._平台字段:
            values[name] = "secret-value-" + name
        values.update({
            "public-provider": "tailscale",
            "public-url": "https://munaigongsi.example.ts.net",
            "openai-base": "https://models.example/v1",
            "qwen-base": "https://models.example/v1",
            "glm-base": "https://models.example/v1",
            "deepseek-base": "https://models.example/v1",
            "embed-usd-per-mtokens": "0.1",
        })
        with patch.object(平台命令, "读取平台", side_effect=lambda name: values[name]):
            env = 平台命令._compose_env(provider="tailscale")
        self.assertEqual(env["XJ_LOCAL_TEST_MODE"], "0")
        self.assertEqual(env["CF_TUNNEL_TOKEN"], "")

    def test_SunnyNgrok公网模式需要客户端令牌且不要求Cloudflare令牌(self):
        values = {name: "secret-value-" + name for name in 平台命令._内部凭据}
        for name in 平台命令._平台字段:
            values[name] = "secret-value-" + name
        values.update({
            "public-provider": "sunny",
            "public-url": "https://company.free.idcfengye.com",
            "sunny-token": "sunny-client-token",
            "openai-base": "https://models.example/v1",
            "qwen-base": "https://models.example/v1",
            "glm-base": "https://models.example/v1",
            "deepseek-base": "https://models.example/v1",
            "embed-usd-per-mtokens": "0.1",
        })
        with patch.object(平台命令, "读取平台", side_effect=lambda name: values[name]):
            env = 平台命令._compose_env(provider="sunny")
        self.assertEqual(env["XJ_LOCAL_TEST_MODE"], "0")
        self.assertEqual(env["CF_TUNNEL_TOKEN"], "")

    def test_公网入口只接受_https_根地址(self):
        平台命令._验证("public-url", "https://company.example")
        平台命令._验证("public-url", "http://localhost:37656")
        for value in ("http://company.example", "https://company.example/path", "https://user@company.example"):
            with self.assertRaises(RuntimeError):
                平台命令._验证("public-url", value)

    def test_公网入口方式只接受现役选项(self):
        for value in ("local", "cloudflare", "tailscale", "sunny"):
            平台命令._验证("public-provider", value)
        with self.assertRaises(RuntimeError):
            平台命令._验证("public-provider", "ngrok")

    def test_只有现役批准中转站可使用_http(self):
        平台命令._验证("openai-base", "http://192.0.2.10/v1")
        with self.assertRaises(RuntimeError):
            平台命令._验证("qwen-base", "http://models.example/v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
