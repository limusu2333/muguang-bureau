PY := .venv/bin/python3

.PHONY: office office-mock roll-call local-alert-model local-reranker-model compile contracts test unit frontend frontend-ci scripts check release-check backup backup-schedule restore-drill

office:
	$(PY) -u "$(CURDIR)/工具/机房.py"

office-mock:
	XJ_MOCK=1 $(PY) -u "$(CURDIR)/工具/机房.py"

roll-call:
	$(PY) 工具/模型接入.py

local-alert-model:
	$(PY) 工具/下载本地告警模型.py

local-reranker-model:
	$(PY) 工具/下载本地精排模型.py

compile:
	$(PY) -m compileall -q 工具 测试

contracts:
	$(PY) -c "import sys; sys.path.insert(0,'工具'); import 部门,房间注册; p=部门.校验()+房间注册.校验(); print('契约自检通过' if not p else '\n'.join(p)); raise SystemExit(bool(p))"

test:
	$(PY) 测试/回归.py

unit:
	$(PY) -m unittest discover -s 测试 -p 'test_*.py' -v

frontend:
	npm --prefix 前端 run build

frontend-ci:
	npm --prefix 前端 ci
	npm --prefix 前端 run build

scripts:
	zsh -n 打开办公室.command
	zsh -n 备份公司记忆.command

check: compile contracts test unit frontend scripts

release-check:
	$(PY) -m 多用户.部署.候选验收 --root . --python $(PY)

backup:
	$(PY) 工具/备份.py

backup-schedule:
	$(PY) 工具/备份任务.py --install

restore-drill:
	$(PY) 工具/备份.py --restore-drill $(if $(strip $(BACKUP)),"$(BACKUP)")
