#!/usr/bin/env bash
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

suffix=$$
tmp=$(mktemp -d /tmp/xj-live-check.XXXXXX)
container_a=xj-live-a-$suffix
container_b=xj-live-b-$suffix
data_a=xj-live-data-a-$suffix
data_b=xj-live-data-b-$suffix
work_a=xj-live-work-a-$suffix
work_b=xj-live-work-b-$suffix

cleanup() {
  docker stop "$container_a" "$container_b" >/dev/null 2>&1 || true
  docker rm "$container_a" "$container_b" >/dev/null 2>&1 || true
  docker volume rm "$data_a" "$data_b" "$work_a" "$work_b" >/dev/null 2>&1 || true
  chmod -R u+w "$tmp" >/dev/null 2>&1 || true
  rm -r "$tmp" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

cp 多用户/config/instance.example.yaml "$tmp/a.yaml"
cp 多用户/config/instance.example.yaml "$tmp/b.yaml"
perl -0pi -e 's/display_name: 示例用户/display_name: 张三/; s/称呼: 示例用户/称呼: 张先生/' "$tmp/a.yaml"
perl -0pi -e 's/display_name: 示例用户/display_name: 王五/; s/称呼: 示例用户/称呼: 王女士/; s/acc_01JEXAMPLE0000000000000000/acc_01JEXAMPLE0000000000000001/g; s/inst_01JEXAMPLE000000000000000/inst_01JEXAMPLE000000000000001/g; s/own_01JEXAMPLE0000000000000000/own_01JEXAMPLE0000000000000001/g' "$tmp/b.yaml"
mkdir "$tmp/policy-a" "$tmp/policy-b"

render_policy() {
  local config=$1
  local output=$2
  docker run --rm --network none --user 0:0 --read-only \
    --tmpfs /tmp:rw,nosuid,nodev,size=64m \
    --mount "type=bind,source=$config,target=/instance/instance.yaml,readonly" \
    --mount "type=bind,source=$output,target=/policy" \
    --mount "type=bind,source=$ROOT/本机私密扫描词.txt,target=/private-terms.txt,readonly" \
    xj-company:dev python3 /app/多用户/渲染制度.py \
    --source /opt/xj-policy-template --output /policy \
    --config /instance/instance.yaml --private-terms /private-terms.txt >/dev/null
}

render_policy "$tmp/a.yaml" "$tmp/policy-a"
render_policy "$tmp/b.yaml" "$tmp/policy-b"

for volume in "$data_a" "$data_b" "$work_a" "$work_b"; do
  docker volume create --label xj.multiuser-test=1 "$volume" >/dev/null
done
for spec in "$data_a:/data" "$data_b:/data" "$work_a:/work" "$work_b:/work"; do
  docker run --rm --network none --user 0:0 \
    --mount "type=volume,source=${spec%%:*},target=${spec#*:}" \
    xj-company:dev /bin/sh -c "chown 10001:10001 ${spec#*:}" >/dev/null
done

start_instance() {
  local name=$1
  local config=$2
  local policy=$3
  local data=$4
  local work=$5
  local mounts=()
  local item
  for item in 班规.md 花名册.yaml 岗位 技能 README.md 公司是谁_为什么干活.md \
    公司的真谛_人是使用者平台是死物.md 会议的真谛_人类开会的意义与真正形式.md \
    公司的工作模式_模型与平台分工.md; do
    mounts+=(--mount "type=bind,source=$policy/$item,target=/app/$item,readonly")
  done
  docker run --detach --name "$name" --network none --user 10001:10001 \
    --read-only --cap-drop ALL --security-opt no-new-privileges:true \
    --tmpfs /tmp:rw,nosuid,nodev,size=64m \
    --tmpfs /home/app:rw,nosuid,nodev,size=32m \
    --mount "type=volume,source=$data,target=/data" \
    --mount "type=volume,source=$work,target=/work" \
    --mount "type=bind,source=$config,target=/instance/instance.yaml,readonly" \
    "${mounts[@]}" \
    --env XJ_MODE=remote --env XJ_CODE_ROOT=/app --env XJ_DATA_ROOT=/data \
    --env XJ_WORKSPACE_ROOT=/work --env XJ_INSTANCE_CONFIG=/instance/instance.yaml \
    --env XJ_OFFICE_PORT=8000 --env OPENAI_API_KEY=test --env DASHSCOPE_API_KEY=test \
    --env ZHIPU_API_KEY=test --env DEEPSEEK_API_KEY=test --env EDITH_DASHSCOPE_KEY=test \
    --env XJ_CONTROL_TOKEN=test xj-company:dev >/dev/null
}

start_instance "$container_a" "$tmp/a.yaml" "$tmp/policy-a" "$data_a" "$work_a"
start_instance "$container_b" "$tmp/b.yaml" "$tmp/policy-b" "$data_b" "$work_b"

ready=0
for _ in $(seq 1 45); do
  if docker exec "$container_a" python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/state",timeout=2).read()' >/dev/null 2>&1 \
    && docker exec "$container_b" python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/state",timeout=2).read()' >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  docker logs "$container_a"
  docker logs "$container_b"
  exit 1
fi

read_identity='import json,urllib.request; s=urllib.request.urlopen("http://127.0.0.1:8000/instance.js").read().decode(); d=json.loads(s.removeprefix("window.__XJ_INSTANCE__=").removesuffix(";\n")); print(d["displayName"]+"/"+d["callName"])'
identity_a=$(docker exec "$container_a" python3 -c "$read_identity")
identity_b=$(docker exec "$container_b" python3 -c "$read_identity")
index_hook=$(docker exec "$container_a" python3 -c 'import urllib.request; print("/instance.js" in urllib.request.urlopen("http://127.0.0.1:8000/").read().decode())')

docker exec "$container_a" python3 -c 'from pathlib import Path; Path("/data/only-a.txt").write_text("A",encoding="utf-8"); Path("/work/only-a.txt").write_text("A",encoding="utf-8")'
isolated=$(docker exec "$container_b" python3 -c 'from pathlib import Path; print(not Path("/data/only-a.txt").exists() and not Path("/work/only-a.txt").exists())')
uid_a=$(docker exec "$container_a" id -u)
readonly_a=$(docker exec "$container_a" python3 -c 'from pathlib import Path
try:
 Path("/app/should-not-write").write_text("x")
 print(False)
except OSError:
 print(True)')

printf 'identity_a=%s\nidentity_b=%s\nindex_hook=%s\ndata_work_isolated=%s\nuid=%s\ncode_readonly=%s\n' \
  "$identity_a" "$identity_b" "$index_hook" "$isolated" "$uid_a" "$readonly_a"

test "$identity_a" = "张三/张先生"
test "$identity_b" = "王五/王女士"
test "$index_hook" = "True"
test "$isolated" = "True"
test "$uid_a" = "10001"
test "$readonly_a" = "True"
