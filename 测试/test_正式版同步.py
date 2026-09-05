from __future__ import annotations

import unittest

from 多用户.部署.正式版同步 import 发布正式版, 正式版错误, 同步状态


class 旧发布器停用测试(unittest.TestCase):
    def test_旧现场合并入口不再可执行(self) -> None:
        with self.assertRaisesRegex(正式版错误, "旧发布器已停用"):
            发布正式版("twilight-v1.0-beta")

    def test_旧同步状态不会宣称可以发布(self) -> None:
        self.assertEqual(同步状态(), {"ok": False, "reason": "旧发布时合并器已停用"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
