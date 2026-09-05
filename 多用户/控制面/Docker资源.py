"""Fixed-name Docker operations for one isolated account."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


_资源名 = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}")
_用户网络名 = re.compile(r"net-acc_[0-9a-hjkmnp-tv-z]{26}")
_制度挂载 = (
    "班规.md", "花名册.yaml", "岗位", "技能", "README.md", "公司是谁_为什么干活.md",
    "公司的真谛_人是使用者平台是死物.md", "会议的真谛_人类开会的意义与真正形式.md",
    "公司的工作模式_模型与平台分工.md",
)


class Docker错误(RuntimeError):
    pass


class Docker资源:
    def __init__(self) -> None:
        self.docker = os.environ.get("XJ_DOCKER_BIN", "docker")
        self.gateway = os.environ.get("XJ_GATEWAY_CONTAINER", "xj-gw")
        self.model_proxy = os.environ.get("XJ_MODEL_PROXY_CONTAINER", "xj-model-proxy")
        self.search_gateway = os.environ.get("XJ_SEARCH_GATEWAY_CONTAINER", "xj-search-gw")
        # The existing rerank bridge also carries the host-only alert endpoint.
        # Attach it to each isolated account network under a separate alias;
        # the account never receives MLX or the host loopback directly.
        self.alert_bridge = os.environ.get("XJ_ALERT_BRIDGE_CONTAINER", "xj-local-rerank-bridge")
        for name in (self.gateway, self.model_proxy, self.search_gateway, self.alert_bridge):
            if not _资源名.fullmatch(name):
                raise Docker错误("共享容器名称无效")

    def _run(self, args: list[str], *, timeout: int = 60, allow_failure: bool = False) -> str:
        try:
            result = subprocess.run(
                [self.docker, *args], stdin=subprocess.DEVNULL, text=True,
                capture_output=True, timeout=timeout, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise Docker错误("Docker 当前不可用") from exc
        if result.returncode != 0 and not allow_failure:
            detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["未知错误"]
            raise Docker错误("Docker 操作失败：" + detail[0][:300])
        return result.stdout.strip()

    def _存在(self, kind: str, name: str) -> bool:
        if kind not in {"container", "network", "volume"} or not _资源名.fullmatch(name):
            raise Docker错误("Docker 资源检查参数无效")
        try:
            result = subprocess.run(
                [self.docker, kind, "inspect", name], stdin=subprocess.DEVNULL, text=True,
                capture_output=True, timeout=20, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise Docker错误("Docker 当前不可用") from exc
        if result.returncode == 0:
            return True
        detail = ((result.stderr or "") + "\n" + (result.stdout or "")).lower()
        if "no such" in detail or "not found" in detail:
            return False
        raise Docker错误("Docker 当前不可用，无法确认资源状态")

    def 容器镜像(self, container: str) -> str | None:
        if not _资源名.fullmatch(str(container or "")):
            raise Docker错误("容器名称无效")
        if not self._存在("container", container):
            return None
        value = self._run(["container", "inspect", "--format", "{{.Config.Image}}", container])
        image = value.strip()
        if not image or any(char.isspace() for char in image):
            raise Docker错误("无法确认用户容器实际镜像")
        return image

    @staticmethod
    def 名称(account_id: str) -> dict[str, str]:
        slug = account_id.lower()
        values = {
            "container": f"company-{slug}",
            "network": f"net-{slug}",
            "data_volume": f"data-{slug}",
            "work_volume": f"work-{slug}",
        }
        if any(not _资源名.fullmatch(value) for value in values.values()):
            raise Docker错误("账户无法映射为安全 Docker 资源名")
        return values

    def 创建网络(self, network: str) -> None:
        self._run(["network", "create", "--internal", "--label", "xj.multiuser=1", network])
        try:
            self._run(["network", "connect", "--alias", "control-gw", network, self.gateway])
            self._run(["network", "connect", "--alias", "model-proxy", network, self.model_proxy])
            self._run(["network", "connect", "--alias", "search-gw", network, self.search_gateway])
            self._run(["network", "connect", "--alias", "alert-render", network, self.alert_bridge])
        except Exception:
            self.删除网络(network)
            raise

    def 恢复共享网络(self) -> int:
        raw = self._run([
            "network", "ls", "--filter", "label=xj.multiuser=1", "--format", "{{.Name}}",
        ])
        networks = [name.strip() for name in raw.splitlines() if name.strip()]
        if any(not _用户网络名.fullmatch(network) for network in networks):
            raise Docker错误("发现名称异常的多用户网络，拒绝自动连接")
        repaired = 0
        expected = (
            (self.gateway, "control-gw"),
            (self.model_proxy, "model-proxy"),
            (self.search_gateway, "search-gw"),
            (self.alert_bridge, "alert-render"),
        )
        for network in networks:
            members_raw = self._run(["network", "inspect", "--format", "{{json .Containers}}", network])
            members = json.loads(members_raw or "{}")
            names = {
                str(item.get("Name") or "")
                for item in members.values()
                if isinstance(item, dict)
            }
            for container, alias in expected:
                if container not in names:
                    self._run(["network", "connect", "--alias", alias, network, container])
                    repaired += 1
        return repaired

    def 删除网络(self, network: str, *, strict: bool = False) -> None:
        for container in (self.gateway, self.model_proxy, self.search_gateway, self.alert_bridge):
            self._run(["network", "disconnect", "-f", network, container], allow_failure=True)
        if self._存在("network", network):
            self._run(["network", "rm", network], allow_failure=not strict)
        if strict and self._存在("network", network):
            raise Docker错误("用户网络未能删除")

    def 创建卷(self, data_volume: str, work_volume: str, image: str) -> None:
        # `docker volume create` silently accepts an existing name, so reject it first.
        for volume in (data_volume, work_volume):
            if self._存在("volume", volume):
                raise Docker错误(f"Docker 卷已存在：{volume}")
        created: list[str] = []
        try:
            self._run(["volume", "create", "--label", "xj.multiuser=1", data_volume])
            created.append(data_volume)
            self._run(["volume", "create", "--label", "xj.multiuser=1", work_volume])
            created.append(work_volume)
            script = (
                "set -eu; "
                "install -d -o 10001 -g 10001 "
                "/data/工单/待确认 /data/工单/执行中 /data/工单/待验收 "
                "/data/工单/已完成 /data/工单/回炉 /data/工单/已作废 "
                "/data/运行状态 /data/执行室/记录 /data/附件 /data/备份 /work; "
                "chown -R 10001:10001 /data /work"
            )
            self._run([
                "run", "--rm", "--network", "none", "--user", "0:0",
                "--mount", f"type=volume,source={data_volume},target=/data",
                "--mount", f"type=volume,source={work_volume},target=/work",
                image, "/bin/sh", "-c", script,
            ], timeout=120)
        except Exception:
            for volume in reversed(created):
                self._run(["volume", "rm", "-f", volume], allow_failure=True)
            raise

    def 删除卷(self, data_volume: str, work_volume: str, *, strict: bool = False) -> None:
        for volume in (data_volume, work_volume):
            if self._存在("volume", volume):
                self._run(["volume", "rm", "-f", volume], allow_failure=not strict)
            if strict and self._存在("volume", volume):
                raise Docker错误(f"用户数据卷未能删除：{volume}")

    def 渲染制度(self, *, image: str, config: Path, output: Path, private_terms: Path) -> None:
        output.mkdir(parents=True, exist_ok=False)
        try:
            self._run([
                "run", "--rm", "--network", "none", "--user", "0:0",
                "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
                "--mount", f"type=bind,source={config},target=/instance/instance.yaml,readonly",
                "--mount", f"type=bind,source={output},target=/policy",
                "--mount", f"type=bind,source={private_terms},target=/private-terms.txt,readonly",
                image, "python3", "/app/多用户/渲染制度.py",
                "--source", "/opt/xj-policy-template", "--output", "/policy",
                "--config", "/instance/instance.yaml", "--private-terms", "/private-terms.txt",
            ], timeout=120)
        except Exception:
            shutil.rmtree(output, ignore_errors=True)
            raise

    def 创建并启动实例(
        self,
        *,
        account_id: str,
        resources: dict[str, str],
        image: str,
        config: Path,
        policy: Path,
        chat_key: str,
        search_token: str,
    ) -> None:
        mounts = [
            "--mount", f"type=volume,source={resources['data_volume']},target=/data",
            "--mount", f"type=volume,source={resources['work_volume']},target=/work",
            "--mount", f"type=bind,source={config},target=/instance/instance.yaml,readonly",
        ]
        for name in _制度挂载:
            source = policy / name
            if not source.exists():
                raise Docker错误(f"制度产物缺少 {name}")
            mounts.extend(["--mount", f"type=bind,source={source},target=/app/{name},readonly"])
        env: list[str] = []
        values = {
            "XJ_MODE": "remote", "XJ_CODE_ROOT": "/app", "XJ_DATA_ROOT": "/data",
            "XJ_WORKSPACE_ROOT": "/work", "XJ_INSTANCE_CONFIG": "/instance/instance.yaml",
            "XJ_OFFICE_PORT": "8000", "DASHSCOPE_HTTP_BASE_URL": "http://search-gw:4100",
            # Formal Linux users call the single host model through the
            # existing internal bridge; they never receive MLX or a model path.
            "XJ_ALERT_RENDER_URL": "http://alert-render:4101",
            "XJ_ALERT_RENDER_REQUIRED": "1",
            "XJ_ALERT_RENDER_TOKEN": os.environ.get("XJ_RERANK_TOKEN", ""),
            "OPENAI_API_KEY": chat_key, "DASHSCOPE_API_KEY": chat_key,
            "ZHIPU_API_KEY": chat_key, "DEEPSEEK_API_KEY": chat_key,
            "EDITH_DASHSCOPE_KEY": search_token, "XJ_CONTROL_TOKEN": search_token,
            "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/home/app",
            "XJ_IMAGE_VERSION": image,
        }
        for key, value in values.items():
            env.extend(["--env", f"{key}={value}"])
        created = False
        try:
            self._run([
                "create", "--name", resources["container"], "--hostname", resources["container"],
                "--label", "xj.multiuser=1", "--label", f"xj.account_id={account_id}",
                "--network", resources["network"], "--user", "10001:10001",
                "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--pids-limit", "256", "--memory", "2g", "--cpus", "2",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
                "--tmpfs", "/home/app:rw,nosuid,nodev,size=64m",
                "--restart", "unless-stopped",
                "--health-cmd", "python3 -c 'import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8000/state\",timeout=3).read()'",
                "--health-interval", "10s", "--health-timeout", "5s", "--health-retries", "6",
                *mounts, *env, image,
            ], timeout=120)
            created = True
            self._run(["start", resources["container"]], timeout=60)
            self._等健康(resources["container"])
        except Exception:
            if created:
                self.删除容器(resources["container"])
            raise

    def _等健康(self, container: str) -> None:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            status = self._run(["inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", container])
            if status == "healthy":
                return
            if status == "unhealthy":
                break
            time.sleep(2)
        raise Docker错误("用户实例启动后未通过健康检查")

    def 停止(self, container: str) -> None:
        self._run(["stop", "--time", "20", container], timeout=40)

    def 创建用户备份(self, container: str) -> str:
        output = self._run(
            ["exec", container, "python3", "/app/工具/备份.py"], timeout=300,
        )
        marker = "已创建并验证："
        lines = [line.strip() for line in output.splitlines() if marker in line]
        if len(lines) != 1:
            raise Docker错误("升级前用户备份没有返回有效路径")
        return lines[0].split(marker, 1)[1].strip()

    def 重启(self, container: str) -> None:
        self._run(["start", container], timeout=60)
        self._等健康(container)

    def 重载(self, container: str) -> None:
        self._run(["restart", "--time", "20", container], timeout=60)
        self._等健康(container)

    def 删除容器(self, container: str, *, strict: bool = False) -> None:
        if self._存在("container", container):
            self._run(["rm", "-f", container], allow_failure=not strict)
        if strict and self._存在("container", container):
            raise Docker错误("用户容器未能删除")

    def 状态(self, container: str) -> dict[str, Any]:
        raw = self._run(["inspect", "--format", "{{json .State}}", container])
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
