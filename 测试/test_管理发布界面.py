from __future__ import annotations

import unittest
from pathlib import Path


class 管理发布界面契约测试(unittest.TestCase):
    根目录 = Path(__file__).resolve().parents[1]
    静态目录 = 根目录 / "多用户" / "界面" / "static"

    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (cls.静态目录 / "admin.html").read_text(encoding="utf-8")
        cls.js = (cls.静态目录 / "admin.js").read_text(encoding="utf-8")
        cls.css = (cls.静态目录 / "admin.css").read_text(encoding="utf-8")

    def test_发布弹窗明确开发版和分发账号边界(self):
        self.assertIn('id="release-track-note"', self.html)
        self.assertIn("开发版仅供管理员验收与生成候选；分发账号继续运行已发布正式版。", self.html)
        self.assertIn("releaseTrackCopy.textContent", self.js)

    def test_管理页按运行环境分开发布和维护(self):
        self.assertIn('id="admin-context-label"', self.html)
        self.assertIn('id="release-kicker"', self.html)
        self.assertIn('id="release-development-source"', self.html)
        self.assertIn("/admin/runtime-context", self.js)
        self.assertIn("正式运行维护", self.js)
        self.assertIn("这是正式运行维护页。候选和发布只能从开发管理页操作。", self.js)
        self.assertIn("releaseConfirm.hidden = !development || running", self.js)
        self.assertIn("releaseNotesWrap.hidden = !development", self.js)

    def test_发布后台过期时一键同时重启两边后台(self):
        self.assertIn("releaseStatus.development_admin", self.js)
        self.assertIn("reload_required", self.js)
        self.assertIn("restartBackendsForRelease", self.js)
        self.assertIn("/admin/system-restart", self.js)
        self.assertIn("两边后台都已加载完成。请重新确认发布。", self.js)

    def test_首轮引导只从最新部署详情读取且普通发布隐藏(self):
        self.assertIn("releaseStatus?.latest_deployment", self.js)
        self.assertIn("details?.bootstrap", self.js)
        self.assertIn("bootstrap.required !== false", self.js)
        self.assertIn("operation?.type !== 'candidate'", self.js)
        self.assertIn("releaseBootstrap.hidden = !visible", self.js)
        self.assertIn("bootstrap.from_legacy_version", self.js)
        self.assertIn("bootstrap.target_version", self.js)
        self.assertIn("host_switch?.mode", self.js)
        self.assertIn('id="release-bootstrap"', self.html)
        self.assertIn(".release-bootstrap", self.css)

    def test_界面不提供独立宿主迁移入口(self):
        self.assertNotIn("/admin/releases/host-boundary/migrate", self.js)
        self.assertNotIn("/admin/releases/host-boundary/migrate", self.html)
        self.assertNotIn("迁移宿主运行面", self.html)

    def test_旧候选随开发版变化后回到重新生成(self):
        self.assertIn("function releaseCandidateIsStale", self.js)
        self.assertIn("开发版已经更新，旧候选已过期；请重新生成候选。", self.js)
        self.assertIn("已过期", self.js)

    def test_公告可以打开原内容编辑并保存回原记录(self):
        self.assertIn('data-announcement-action="edit"', self.js)
        self.assertIn("openAnnouncementDialog(announcement)", self.js)
        self.assertIn("method: editing ? 'PATCH' : 'POST'", self.js)
        self.assertIn("公告修改已保存", self.js)
        self.assertIn('id="announcement-dialog-title"', self.html)
        self.assertIn('/admin-ui/admin.js?v=20260904-announcement-history-edit', self.html)
        self.assertIn('/admin-ui/admin.css?v=20260904-announcement-history-edit', self.html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
