PYTHON := .venv/bin/python
LAWS := $(PYTHON) -m lawref
# 装好后 .venv/bin/laws 也能直接用
INSTALLED := .venv/.installed

.DEFAULT_GOAL := shell
.PHONY: shell lint lint-fix db test check setup serve browser

# 交互菜单：抓取、整理、检查、更新数据库、发布
shell: $(INSTALLED)
	@$(LAWS) shell

lint: $(INSTALLED)
	@$(LAWS) lint

# 自动修复能按规则修好的格式问题（含 DLC），再检查一遍
lint-fix: $(INSTALLED)
	@$(LAWS) lint --dlc --fix

# 先更新本体，再更新全部 DLC
db: $(INSTALLED)
	@$(LAWS) db update

# Web 管理端：采集、查看法规、跑任务
serve: $(INSTALLED)
	@.venv/bin/laws serve

test: $(INSTALLED)
	@$(PYTHON) -m pytest scripts/tests

# 代码风格检查
check: $(INSTALLED)
	@$(PYTHON) -m ruff check scripts

# 无头浏览器兜底：JS 渲染、JS 验证页的原文
browser: | $(PYTHON)
	$(PYTHON) -m pip install -e 'scripts[browser]'
	$(PYTHON) -m playwright install chromium

# 依赖有变化时手动运行
setup: | $(PYTHON)
	$(PYTHON) -m pip install -e 'scripts[dev]'
	@touch $(INSTALLED)

$(INSTALLED): scripts/pyproject.toml | $(PYTHON)
	$(PYTHON) -m pip install -e 'scripts[dev]'
	@touch $(INSTALLED)

# 只在没有虚拟环境时创建，已有的不会重建
$(PYTHON):
	python3 -m venv .venv
